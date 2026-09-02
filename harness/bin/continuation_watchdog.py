#!/usr/bin/env python3
"""Local-model continuation monitor for quiescent Claude and Codex sessions."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import pathlib
import subprocess
import time
from typing import Callable, Optional

import session_observer as observer
import winddown_judge

QUIET_SECONDS = 180
DISCOVERY_LOOKBACK_SECONDS = 2 * 24 * 60 * 60
EVALUATION_BACKOFF_SECONDS = 300
MAX_EVALUATION_BACKOFF_SECONDS = 3600
MAX_EVALUATIONS_PER_PASS = 2
LAUNCH_GRACE_SECONDS = 180
LEASE_SECONDS = 120
MAX_HOST_ATTEMPTS = 3
PROMPT_VERSION = 1
VALID_PHASES = {
    "pending", "baseline", "delivered", "not_needed", "invalid", "exhausted",
    "superseded", "launching", "launched",
}


def marker_for(delivery_key: str) -> str:
    digest = hashlib.sha256(delivery_key.encode()).hexdigest()[:20]
    return f"[escapement-continuation:{digest}]"


def continuation_prompt(candidate: observer.Candidate) -> str:
    return (
        f"{marker_for(candidate.delivery_key)} Escapement detected that your prior turn "
        "ended while reversible work already requested by the user remained unfinished. "
        "Continue now with the next concrete in-scope action. Do not stop merely to list "
        "remaining steps or ask permission for ordinary delegated work. Finish and verify "
        "the requested outcome, or name a genuine external blocker that requires the user."
    )


def _read_state(path: pathlib.Path) -> dict:
    try:
        value = json.loads(path.read_text())
        if not isinstance(value, dict):
            return {}
        if value.get("version") not in (None, 1) or not isinstance(
            value.get("initialized", False), bool
        ):
            return {}
        deliveries = value.get("deliveries", {})
        if not isinstance(deliveries, dict) or any(
            not isinstance(key, str) or not isinstance(record, dict)
            for key, record in deliveries.items()
        ):
            return {}
        if "last_scan_started" in value and not isinstance(
            value["last_scan_started"], (int, float)
        ):
            return {}
        numeric_fields = {
            "seen_at", "delivered_at", "next_evaluation_at", "retry_after",
            "lease_until", "launched_at", "exhausted_at", "attempts",
            "evaluation_failures",
        }
        for record in deliveries.values():
            if any(
                field in record and not isinstance(record[field], (int, float))
                for field in numeric_fields
            ):
                return {}
            if "phase" in record and (
                not isinstance(record["phase"], str)
                or record["phase"] not in VALID_PHASES
            ):
                return {}
            if "verdict" in record and not isinstance(record["verdict"], bool):
                return {}
            if "host" in record and record["host"] not in ("claude", "codex"):
                return {}
            if "transcript" in record and not isinstance(record["transcript"], str):
                return {}
        return value
    except (OSError, json.JSONDecodeError):
        return {}


def _write_state(path: pathlib.Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path.parent, 0o700)
    temporary = path.with_suffix(".tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(state, stream, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _safe_cwd(value: str) -> bool:
    if not value:
        return False
    path = pathlib.Path(value)
    try:
        stat = path.stat()
        return path.is_absolute() and path.is_dir() and stat.st_uid == os.getuid()
    except OSError:
        return False


def _active_claude_ids() -> Optional[set[str]]:
    try:
        result = subprocess.run(
            ["claude", "agents", "--json"], capture_output=True, text=True,
            timeout=15, check=False,
        )
        if result.returncode != 0:
            return None
        rows = json.loads(result.stdout)
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
        return None
    if not isinstance(rows, list):
        return None
    found = set()
    for row in rows:
        if isinstance(row, dict):
            value = row.get("session_id") or row.get("sessionId") or row.get("id")
            if isinstance(value, str):
                found.add(value)
    return found


def _codex_writer_active(session_id: str, locks_root: Optional[pathlib.Path] = None) -> bool:
    root = locks_root or pathlib.Path.home() / ".codex" / "thread-writer-locks"
    path = root / f"{session_id}.lock"
    try:
        stream = path.open("a+")
    except OSError:
        return False
    try:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        fcntl.flock(stream, fcntl.LOCK_UN)
        return False
    finally:
        stream.close()


def _launch(candidate: observer.Candidate, prompt: str, codex_active: bool) -> bool:
    child_env = dict(os.environ)
    child_env.pop("ESCAPEMENT_LOCAL_JUDGE_API_KEY", None)
    child_env.pop("ESCAPEMENT_LOCAL_JUDGE_API_KEY_FILE", None)
    try:
        if candidate.host == "codex" and codex_active:
            result = subprocess.run(
                ["codex", "queue", "--thread", candidate.session_id, "--message", prompt],
                cwd=candidate.cwd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, timeout=20, check=False, env=child_env,
            )
            return result.returncode == 0
        if candidate.host == "codex":
            process = subprocess.Popen(
                ["codex", "exec", "resume", "--json", candidate.session_id, "-"],
                cwd=candidate.cwd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, text=True, start_new_session=True,
                env=child_env,
            )
            assert process.stdin is not None
            process.stdin.write(prompt)
            process.stdin.close()
            try:
                return process.wait(timeout=1) == 0
            except subprocess.TimeoutExpired:
                return True
        process = subprocess.Popen(
            ["claude", "--resume", candidate.session_id, "-p", prompt],
            cwd=candidate.cwd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, start_new_session=True, env=child_env,
        )
        try:
            return process.wait(timeout=1) == 0
        except subprocess.TimeoutExpired:
            return True
    except (OSError, BrokenPipeError, subprocess.TimeoutExpired):
        return False


def _still_current(candidate: observer.Candidate) -> Optional[observer.Candidate]:
    parser = observer.parse_claude if candidate.host == "claude" else observer.parse_codex
    try:
        current = parser(candidate.transcript)
    except (OSError, ValueError, TypeError, AttributeError):
        return None
    if current is None or current.delivery_key != candidate.delivery_key:
        return None
    return current


def _candidate_from_record(record: dict) -> Optional[observer.Candidate]:
    transcript = record.get("transcript")
    host = record.get("host")
    if not isinstance(transcript, str) or host not in ("claude", "codex"):
        return None
    parser = observer.parse_claude if host == "claude" else observer.parse_codex
    try:
        return parser(pathlib.Path(transcript))
    except (OSError, ValueError, TypeError, AttributeError):
        return None


def run_once(
    state_root: pathlib.Path,
    *,
    claude_root: Optional[pathlib.Path] = None,
    codex_root: Optional[pathlib.Path] = None,
    now: Optional[float] = None,
    discoverer: Optional[Callable[..., list[observer.Candidate]]] = None,
    judge: Optional[Callable[..., Optional[bool]]] = None,
    launcher: Optional[Callable[[observer.Candidate, str, bool], bool]] = None,
    active_claude_ids: Optional[Callable[[], Optional[set[str]]]] = None,
    codex_writer_active: Optional[Callable[[str], bool]] = None,
) -> dict:
    """Run one bounded pass. State contains identifiers and counters, never prompts."""
    current_time = time.time() if now is None else now
    state_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock_path = state_root / "watchdog.lock"
    lock = lock_path.open("a+")
    try:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {"locked": True, "launched": 0, "evaluated": 0}
        state_path = state_root / "state.json"
        state = _read_state(state_path)
        deliveries = state.setdefault("deliveries", {})
        find = discoverer or observer.discover
        modified_since = float(
            state.get("last_scan_started", current_time - DISCOVERY_LOOKBACK_SECONDS)
        ) - 1
        candidates = find(
            claude_root or pathlib.Path.home() / ".claude" / "projects",
            codex_root or pathlib.Path.home() / ".codex" / "sessions",
            modified_since=modified_since,
        )
        state["last_scan_started"] = current_time
        if not state.get("initialized"):
            for candidate in candidates:
                deliveries[candidate.delivery_key] = {
                    "phase": "baseline", "seen_at": current_time,
                }
            state.update({"version": 1, "initialized": True})
            _write_state(state_path, state)
            return {"initialized": True, "launched": 0, "evaluated": 0}

        candidate_by_key = {candidate.delivery_key: candidate for candidate in candidates}
        terminal_phases = {
            "baseline", "delivered", "not_needed", "invalid", "exhausted", "superseded",
        }
        for key, record in deliveries.items():
            if record.get("phase") in terminal_phases or key in candidate_by_key:
                continue
            current = _candidate_from_record(record)
            if current is None or current.delivery_key != key:
                record["phase"] = "superseded"
            else:
                candidate_by_key[key] = current

        stats = {
            "initialized": False, "launched": 0, "evaluated": 0,
            "deferred": 0, "exhausted": 0,
        }
        active_claude_fn = active_claude_ids or _active_claude_ids
        judge_fn = judge or winddown_judge.model_verdict
        launch_fn = launcher or _launch
        codex_active_fn = codex_writer_active or _codex_writer_active

        ordered_candidates = sorted(
            candidate_by_key.values(),
            key=lambda candidate: (
                float(deliveries.get(candidate.delivery_key, {}).get("seen_at", current_time)),
                candidate.delivery_key,
            ),
        )
        for candidate in ordered_candidates:
            key = candidate.delivery_key
            record = deliveries.setdefault(key, {
                "phase": "pending", "seen_at": current_time,
                "host": candidate.host, "transcript": str(candidate.transcript),
            })
            phase = record.get("phase")
            if phase in terminal_phases:
                continue
            if current_time - candidate.completed_at < QUIET_SECONDS:
                continue
            marker = marker_for(key)
            if observer.contains_marker(candidate.transcript, marker):
                record.update({"phase": "delivered", "delivered_at": current_time})
                continue
            if phase == "launching" and current_time < record.get("lease_until", 0):
                continue
            if phase == "launched" and current_time < record.get("retry_after", 0):
                continue
            if current_time < record.get("retry_after", 0):
                continue
            if record.get("attempts", 0) >= MAX_HOST_ATTEMPTS:
                if record.get("phase") != "exhausted":
                    stats["exhausted"] += 1
                record.update({"phase": "exhausted", "exhausted_at": current_time})
                continue
            if not _safe_cwd(candidate.cwd):
                record.update({"phase": "invalid", "reason": "unsafe_cwd"})
                continue
            verdict = record.get("verdict")
            if verdict is not True:
                if current_time < record.get("next_evaluation_at", 0):
                    continue
                if stats["evaluated"] >= MAX_EVALUATIONS_PER_PASS:
                    stats["deferred"] += 1
                    continue
                stats["evaluated"] += 1
                try:
                    verdict = judge_fn(candidate.response, user_request=candidate.request)
                except Exception:
                    verdict = None
                if verdict is False:
                    record.update({"phase": "not_needed", "verdict": False})
                    continue
                if verdict is not True:
                    failures = int(record.get("evaluation_failures", 0)) + 1
                    delay = min(
                        MAX_EVALUATION_BACKOFF_SECONDS,
                        EVALUATION_BACKOFF_SECONDS * (2 ** min(failures - 1, 4)),
                    )
                    record.update({
                        "phase": "pending", "evaluation_failures": failures,
                        "next_evaluation_at": current_time + delay,
                    })
                    continue
                record["verdict"] = True

            fresh = _still_current(candidate)
            if fresh is None or observer.contains_marker(candidate.transcript, marker):
                record["phase"] = "delivered" if observer.contains_marker(candidate.transcript, marker) else "superseded"
                continue
            if fresh.host == "claude":
                active_claude = active_claude_fn()
                if active_claude is None or fresh.session_id in active_claude:
                    record.update({
                        "phase": "pending",
                        "next_evaluation_at": current_time + QUIET_SECONDS,
                    })
                    continue
            codex_active = fresh.host == "codex" and codex_active_fn(fresh.session_id)
            record.update({
                "phase": "launching", "attempts": int(record.get("attempts", 0)) + 1,
                "lease_until": current_time + LEASE_SECONDS,
            })
            _write_state(state_path, state)  # durable claim before external acceptance
            try:
                accepted = launch_fn(fresh, continuation_prompt(fresh), codex_active)
            except Exception:
                accepted = False
            if accepted:
                record.update({
                    "phase": "launched", "retry_after": current_time + LAUNCH_GRACE_SECONDS,
                    "launched_at": current_time,
                })
                stats["launched"] += 1
            else:
                record.update({"phase": "pending", "retry_after": current_time + LAUNCH_GRACE_SECONDS})
        _write_state(state_path, state)
        return stats
    finally:
        lock.close()


def main() -> int:
    root = pathlib.Path(
        os.environ.get("CONTINUATION_HARNESS_HOME", pathlib.Path.home() / ".claude" / "harness")
    ) / "watchdog"
    try:
        stats = run_once(root)
    except Exception as exc:
        print(f"continuation watchdog failed: {type(exc).__name__}", file=os.sys.stderr)
        return 1
    print(json.dumps({"continuation_watchdog": stats}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
