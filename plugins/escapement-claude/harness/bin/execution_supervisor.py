#!/usr/bin/env python3
"""Level-triggered reconciliation and fenced recovery for delegated work."""

from __future__ import annotations

import argparse
import copy
import datetime as dt
import fcntl
import json
import os
import pathlib
import stat
import subprocess
import tempfile
import uuid
from collections.abc import Callable

from execution_ledger import apply_event, claim_recovery, reconcile_deadlines
from execution_store import load_trusted, mutate_atomic
from trusted_source import is_trusted_file

UTC = dt.timezone.utc
CLAIM_TTL_SECONDS = 60
DEFAULT_RECOVERY_BUDGET = 3


def _iso(value: dt.datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _parse(value: str) -> dt.datetime:
    parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return parsed.astimezone(UTC)


def _one_exact(records: object, expected_id: str) -> dict | None:
    if not isinstance(records, list) or len(records) != 1:
        return None
    record = records[0]
    if not isinstance(record, dict) or record.get("id") != expected_id:
        return None
    return record


def session_repo_cwd(thread_dir: pathlib.Path, session_id: str) -> pathlib.Path | None:
    """Resolve the existing task-mode repository binding for daemon Beads calls."""
    mode_path = pathlib.Path(thread_dir) / "session_mode.json"
    if not is_trusted_file(mode_path):
        return None
    try:
        mode = json.loads(mode_path.read_text())
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    if not isinstance(mode, dict) or mode.get("mode") != "task":
        return None
    if mode.get("session_id") != session_id:
        return None
    raw_cwd = mode.get("repo_cwd")
    if not isinstance(raw_cwd, str) or not raw_cwd:
        return None
    repo_cwd = pathlib.Path(raw_cwd)
    if not repo_cwd.is_absolute() or not repo_cwd.is_dir():
        return None
    return repo_cwd.resolve()


def _valid_health(value: object) -> bool:
    if not isinstance(value, dict):
        return False
    required = {
        "reconcile_started_at",
        "last_successful_reconcile_at",
        "completed_generation",
        "installation_id",
        "counts",
    }
    if set(value) != required:
        return False
    if not isinstance(value["reconcile_started_at"], (str, type(None))):
        return False
    if not isinstance(value["last_successful_reconcile_at"], (str, type(None))):
        return False
    generation = value["completed_generation"]
    if (
        not isinstance(generation, int)
        or isinstance(generation, bool)
        or generation < 0
    ):
        return False
    return (
        isinstance(value["installation_id"], str)
        and bool(value["installation_id"])
        and isinstance(value["counts"], dict)
    )


def _new_health() -> dict:
    installation_id = os.environ.get("ESCAPEMENT_INSTALLATION_ID") or uuid.uuid4().hex
    return {
        "reconcile_started_at": None,
        "last_successful_reconcile_at": None,
        "completed_generation": 0,
        "installation_id": installation_id,
        "counts": {"successful_passes": 0, "threads": 0, "recoveries": 0},
    }


def _mutate_health(path: pathlib.Path, mutation: Callable[[dict], dict]) -> dict:
    """Durably mutate supervisor health under its own stable lock."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_name(f".{path.name}.lock")
    lock_fd = os.open(
        lock_path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600
    )
    try:
        lock_stat = os.fstat(lock_fd)
        if not stat.S_ISREG(lock_stat.st_mode):
            raise ValueError("health lock is not a regular file")
        os.chmod(lock_path, 0o600)
        with os.fdopen(lock_fd, "r+") as lock_file:
            lock_fd = -1
            fcntl.flock(lock_file, fcntl.LOCK_EX)
            if not is_trusted_file(lock_path):
                raise ValueError("health lock is untrusted")
            if is_trusted_file(path):
                try:
                    current = json.loads(path.read_text())
                except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                    raise ValueError("supervisor health is malformed") from exc
                if not _valid_health(current):
                    raise ValueError("supervisor health is invalid")
            elif os.path.lexists(path):
                raise ValueError("supervisor health is untrusted")
            else:
                current = _new_health()
            updated = mutation(copy.deepcopy(current))
            if not _valid_health(updated):
                raise ValueError("health mutation produced invalid state")
            temporary_name: str | None = None
            try:
                with tempfile.NamedTemporaryFile(
                    mode="w",
                    encoding="utf-8",
                    dir=path.parent,
                    prefix=f".{path.name}.",
                    suffix=".tmp",
                    delete=False,
                ) as temporary:
                    temporary_name = temporary.name
                    os.chmod(temporary_name, 0o600)
                    json.dump(updated, temporary, sort_keys=True, separators=(",", ":"))
                    temporary.write("\n")
                    temporary.flush()
                    os.fsync(temporary.fileno())
                os.replace(temporary_name, path)
                temporary_name = None
                directory_fd = os.open(path.parent, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
            finally:
                if temporary_name is not None:
                    pathlib.Path(temporary_name).unlink(missing_ok=True)
            return updated
    finally:
        if lock_fd >= 0:
            os.close(lock_fd)


def _mark_started(path: pathlib.Path, now: dt.datetime) -> dict:
    def mutate(current: dict) -> dict:
        current["reconcile_started_at"] = _iso(now)
        return current

    return _mutate_health(path, mutate)


def _mark_success(
    path: pathlib.Path, now: dt.datetime, *, threads: int, recoveries: int
) -> dict:
    def mutate(current: dict) -> dict:
        previous = current.get("counts", {})
        current["last_successful_reconcile_at"] = _iso(now)
        current["completed_generation"] += 1
        current["counts"] = {
            "successful_passes": int(previous.get("successful_passes", 0)) + 1,
            "threads": threads,
            "recoveries": recoveries,
        }
        return current

    return _mutate_health(path, mutate)


def _claim_expired(claim: dict | None, now: dt.datetime) -> bool:
    return claim is not None and now.astimezone(UTC) >= _parse(claim["expires_at"])


def _phase(callback, name: str, context: dict) -> None:
    if callback is not None:
        callback(name, copy.deepcopy(context))


def plan_thread(
    thread_dir: pathlib.Path, now: dt.datetime, native_status, run_bd
) -> dict:
    """Read one trusted ledger and resolve external status without mutating it."""
    del now
    path = pathlib.Path(thread_dir) / "executions.json"
    session_id = pathlib.Path(thread_dir).name
    ledger = load_trusted(path, session_id)
    if ledger is None:
        return {"status": "unresolved", "thread": session_id, "ledger_path": path}
    repo_cwd = session_repo_cwd(thread_dir, session_id)
    if ledger["executions"] and repo_cwd is None:
        return {"status": "unresolved", "thread": session_id, "ledger_path": path}
    bd_runner = run_bd or _default_run_bd(repo_cwd)
    events: list[dict] = []
    canonical: dict[str, dict] = {}
    for execution in ledger["executions"]:
        observed = native_status(copy.deepcopy(execution))
        event = None
        status_label = "unknown"
        if isinstance(observed, dict):
            if isinstance(observed.get("event"), dict):
                event = observed["event"]
                status_label = str(observed.get("status") or "unknown")
            elif isinstance(observed.get("kind"), str):
                event = observed
            elif observed.get("status") is not None:
                status_label = str(observed["status"])
        elif isinstance(observed, str) and observed:
            status_label = observed
        if event is not None:
            events.append(event)

        bead_id = execution["bead_id"]
        child = _one_exact(bd_runner(["show", bead_id]), bead_id)
        if child is None:
            return {"status": "unresolved", "thread": session_id, "ledger_path": path}
        parent_id = child.get("parent") or child.get("parent_id")
        if not isinstance(parent_id, str) or not parent_id:
            return {"status": "unresolved", "thread": session_id, "ledger_path": path}
        parent = _one_exact(bd_runner(["show", parent_id]), parent_id)
        if parent is None:
            return {"status": "unresolved", "thread": session_id, "ledger_path": path}
        canonical[execution["execution_id"]] = {
            "child": child,
            "parent": parent,
            "parent_id": parent_id,
            "native_status": status_label,
        }
    return {
        "status": "ok",
        "thread": session_id,
        "ledger_path": path,
        "ledger": ledger,
        "events": events,
        "canonical": canonical,
        "repo_cwd": str(repo_cwd) if repo_cwd is not None else None,
    }


def _deadline(item: dict) -> tuple[str, str]:
    reason = item["reconcile_due"]
    return reason, item[f"{reason}_deadline"]


def _recovery_descriptor(plan: dict, item: dict) -> dict:
    canonical = plan["canonical"][item["execution_id"]]
    reason, deadline = _deadline(item)
    activity_kind = item.get("last_activity_kind") or "none"
    activity_at = item.get("last_activity_at") or "none"
    native_child = item.get("native_child_id") or "none"
    prompt = (
        f"Reconcile delegated execution {item['execution_id']} for parent bead "
        f"{canonical['parent_id']} and child bead {item['bead_id']}; attempt "
        f"{item['attempt']} generation {item['generation']} crossed its {reason} "
        f"deadline {deadline}. Last accepted activity: {activity_kind} at "
        f"{activity_at}. Native child: {native_child}; native status "
        f"{canonical['native_status']}. Inspect durable ledger "
        f"{plan['ledger_path']} and verify the actual outcome before retrying any "
        "external mutation."
    )
    return {
        "host": item["host"],
        "repo_cwd": plan["repo_cwd"],
        "parent_session_id": plan["thread"],
        "parent_bead_id": canonical["parent_id"],
        "bead_id": item["bead_id"],
        "execution_id": item["execution_id"],
        "attempt": item["attempt"],
        "generation": item["generation"],
        "watchdog_id": item["watchdog_id"],
        "reconcile_due": reason,
        "ledger_path": str(plan["ledger_path"]),
        "prompt": prompt,
    }


def _append_budget_escalation(
    current: dict, execution_id: str, now: dt.datetime
) -> dict:
    item = next(
        candidate
        for candidate in current["executions"]
        if candidate["execution_id"] == execution_id
    )
    incident = {
        "type": "recovery_budget_exhausted",
        "execution_id": execution_id,
        "attempt": item["attempt"],
        "generation": item["generation"],
        "recorded_at": _iso(now),
    }
    if not any(
        evidence.get("type") == incident["type"]
        and evidence.get("execution_id") == execution_id
        and evidence.get("attempt") == item["attempt"]
        and evidence.get("generation") == item["generation"]
        for evidence in current["incidents"]
    ):
        current["incidents"].append(incident)
        current["updated_at"] = incident["recorded_at"]
    return current


def reconcile_all(
    threads_root: pathlib.Path,
    now: dt.datetime,
    owner: str,
    spawn: Callable[[dict], object],
    *,
    native_status: Callable[[dict], object] | None = None,
    run_bd: Callable[[list[str]], object] | None = None,
    inspect_scheduled: Callable[[], object] | None = None,
    phase_hook: Callable[[str, dict], None] | None = None,
    recovery_budget: int = DEFAULT_RECOVERY_BUDGET,
    completion_clock: Callable[[], dt.datetime] | None = None,
) -> dict:
    """Reconcile current level state; stamp health only after the full pass."""
    root = pathlib.Path(threads_root)
    health_path = root.parent / "supervisor-health.json"
    _mark_started(health_path, now)
    if inspect_scheduled is not None:
        inspect_scheduled()
    if not root.is_dir():
        return {
            "status": "unresolved",
            "unresolved_threads": [str(root)],
            "recoveries": 0,
        }
    native_status = native_status or (lambda _execution: None)
    unresolved: list[str] = []
    plans: list[dict] = []
    recoveries = 0

    for thread_dir in sorted(path for path in root.glob("*") if path.is_dir()):
        ledger_path = thread_dir / "executions.json"
        if not os.path.lexists(ledger_path):
            continue
        plan = plan_thread(thread_dir, now, native_status, run_bd)
        _phase(phase_hook, "after_plan", {"thread": thread_dir.name})
        if plan["status"] != "ok":
            unresolved.append(thread_dir.name)
            continue
        plans.append(plan)

    for plan in plans:

        def reconcile(current: dict) -> dict:
            for event in plan["events"]:
                apply_event(current, event, now)
            reconcile_deadlines(current, now)
            return current

        current = mutate_atomic(plan["ledger_path"], reconcile)
        for snapshot in list(current["executions"]):
            if snapshot["reconcile_due"] is None or snapshot["state"] in {
                "terminal",
                "cancelled",
            }:
                continue
            execution_id = snapshot["execution_id"]
            if execution_id not in plan["canonical"]:
                unresolved.append(plan["thread"])
                continue
            claim = snapshot["recovery_claim"]
            if claim is not None and not _claim_expired(claim, now):
                continue
            if claim is not None and snapshot["recovery_count"] >= recovery_budget:
                _phase(
                    phase_hook,
                    "before_escalation",
                    {"thread": plan["thread"], "execution_id": execution_id},
                )
                mutate_atomic(
                    plan["ledger_path"],
                    lambda fresh, eid=execution_id: _append_budget_escalation(
                        fresh, eid, now
                    ),
                )
                continue

            _phase(
                phase_hook,
                "before_claim",
                {"thread": plan["thread"], "execution_id": execution_id},
            )
            claimed: dict | None = None

            def acquire(fresh: dict) -> dict:
                nonlocal claimed
                item = next(
                    candidate
                    for candidate in fresh["executions"]
                    if candidate["execution_id"] == execution_id
                )
                existing = item["recovery_claim"]
                if (
                    existing is not None
                    and _claim_expired(existing, now)
                    and item["recovery_count"] >= recovery_budget
                ):
                    return fresh
                claimed = claim_recovery(
                    fresh, execution_id, now, owner, CLAIM_TTL_SECONDS
                )
                return fresh

            claimed_ledger = mutate_atomic(plan["ledger_path"], acquire)
            if claimed is None:
                continue
            _phase(
                phase_hook,
                "after_claim",
                {"thread": plan["thread"], "execution_id": execution_id},
            )
            claimed_item = next(
                item
                for item in claimed_ledger["executions"]
                if item["execution_id"] == execution_id
            )
            descriptor = _recovery_descriptor(plan, claimed_item)
            try:
                spawn(descriptor)
            except OSError:
                unresolved.append(plan["thread"])
                continue
            recoveries += 1
            _phase(
                phase_hook,
                "after_spawn",
                {"thread": plan["thread"], "execution_id": execution_id},
            )

    if unresolved:
        return {
            "status": "unresolved",
            "unresolved_threads": sorted(set(unresolved)),
            "recoveries": recoveries,
        }
    completed_at = completion_clock() if completion_clock is not None else now
    health = _mark_success(
        health_path, completed_at, threads=len(plans), recoveries=recoveries
    )
    return {"status": "ok", "recoveries": recoveries, "health": health}


def _spawn(descriptor: dict) -> list[str]:
    host = descriptor.get("host")
    session_id = descriptor.get("parent_session_id") or ""
    prompt = descriptor.get("prompt") or ""
    if host == "claude":
        return ["claude", "--resume", session_id, "-p", prompt]
    if host == "codex":
        return ["codex", "exec", "resume", session_id, prompt]
    raise ValueError("unsupported execution host")


def launch_in_repo(argv: list[str], repo_cwd: str | pathlib.Path):
    """Launch an argv vector at the already-validated repository boundary."""
    return subprocess.Popen(argv, cwd=repo_cwd)


def launch_recovery(descriptor: dict):
    """Launch one host recovery in the durable originating repository."""
    return launch_in_repo(_spawn(descriptor), descriptor["repo_cwd"])


def _default_run_bd(cwd: pathlib.Path | None):
    def run_bd(args: list[str]):
        try:
            result = subprocess.run(
                ["bd", *args, "--json"],
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=15,
            )
            if result.returncode != 0:
                return None
            value = json.loads(result.stdout)
            return value if isinstance(value, list) else None
        except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
            return None

    return run_bd


def _parse_now(value: str | None) -> dt.datetime:
    return _parse(value) if value else dt.datetime.now(UTC)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Reconcile delegated execution leases")
    parser.add_argument("--threads-root", required=True)
    parser.add_argument("--now")
    parser.add_argument("--owner", default=f"execution-supervisor:{os.getpid()}")
    args = parser.parse_args(argv)

    result = reconcile_all(
        pathlib.Path(args.threads_root),
        _parse_now(args.now),
        args.owner,
        launch_recovery,
        completion_clock=(
            None if args.now else lambda: dt.datetime.now(dt.timezone.utc)
        ),
    )
    print(json.dumps(result, sort_keys=True))
    return 0 if result["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
