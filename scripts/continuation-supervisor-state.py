#!/usr/bin/env python3
"""Trusted filesystem transaction helpers for the continuation LaunchAgent."""

from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import hashlib
import json
import os
import pathlib
import plistlib
import stat
import subprocess
import tempfile


def _trusted_directory(path: pathlib.Path) -> bool:
    try:
        metadata = path.lstat()
    except OSError:
        return False
    return (
        stat.S_ISDIR(metadata.st_mode)
        and metadata.st_uid == os.getuid()
        and stat.S_IMODE(metadata.st_mode) & 0o022 == 0
    )


def _trusted_file(path: pathlib.Path) -> bool:
    if path.is_symlink():
        return False
    try:
        metadata = path.stat()
    except OSError:
        return False
    return (
        stat.S_ISREG(metadata.st_mode)
        and metadata.st_uid == os.getuid()
        and stat.S_IMODE(metadata.st_mode) & 0o022 == 0
    )


def _fsync(path: pathlib.Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _durable_json(path: pathlib.Path, value: object) -> None:
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
            os.fchmod(temporary.fileno(), 0o600)
            json.dump(value, temporary, indent=2, sort_keys=True)
            temporary.write("\n")
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_name, path)
        temporary_name = None
        _fsync(path.parent)
    finally:
        if temporary_name is not None:
            pathlib.Path(temporary_name).unlink(missing_ok=True)


def validate_state(threads_root: pathlib.Path, quarantine_root: pathlib.Path) -> None:
    if os.path.lexists(threads_root):
        if not _trusted_directory(threads_root):
            raise ValueError(f"untrusted scheduled-state directory: {threads_root}")
        for thread_dir in threads_root.iterdir():
            if not thread_dir.is_dir() and not thread_dir.is_symlink():
                continue
            if not _trusted_directory(thread_dir):
                raise ValueError(f"untrusted scheduled-state directory: {thread_dir}")
            scheduled = thread_dir / "scheduled.json"
            if os.path.lexists(scheduled) and not _trusted_file(scheduled):
                raise ValueError(f"untrusted scheduled state: {scheduled}")
    if not os.path.lexists(quarantine_root):
        return
    if not _trusted_directory(quarantine_root):
        raise ValueError(f"untrusted quarantine directory: {quarantine_root}")
    for transaction in quarantine_root.iterdir():
        if not _trusted_directory(transaction):
            raise ValueError(f"untrusted quarantine transaction: {transaction}")
        manifest = transaction / "manifest.json"
        contents = list(transaction.iterdir())
        recoverable_preparation = (
            transaction.name.startswith(".legacy-schedules-preparing-")
            and not os.path.lexists(manifest)
            and all(
                item.name.startswith(".manifest.json.")
                and item.name.endswith(".tmp")
                and _trusted_file(item)
                for item in contents
            )
        )
        if not recoverable_preparation and not _trusted_file(manifest):
            raise ValueError(f"untrusted quarantine manifest: {manifest}")


def marker_status(path: pathlib.Path, label: str) -> str:
    if not os.path.lexists(path):
        return "absent"
    if not _trusted_file(path):
        raise ValueError("continuation supervisor installation marker is untrusted")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(
            "continuation supervisor installation marker is malformed"
        ) from exc
    if value != {"label": label, "version": 1}:
        raise ValueError("continuation supervisor installation marker is invalid")
    return "valid"


def _validate_record(
    record: object,
    transaction: pathlib.Path,
    threads_root: pathlib.Path,
) -> tuple[pathlib.Path, pathlib.Path, str]:
    keys = {"source", "archive", "reason", "sha256"}
    if not isinstance(record, dict) or set(record) != keys:
        raise ValueError("invalid quarantine manifest record")
    source = pathlib.Path(record["source"])
    archive = pathlib.Path(record["archive"])
    try:
        source_root = threads_root.resolve()
        transaction_root = transaction.resolve()
        resolved_source = source.resolve(strict=False)
        resolved_archive = archive.resolve(strict=False)
        resolved_source.relative_to(source_root)
        resolved_archive.relative_to(transaction_root)
    except ValueError as exc:
        raise ValueError("quarantine manifest path escapes its authority root") from exc
    if (
        resolved_source.name != "scheduled.json"
        or resolved_source.parent.parent != source_root
        or not _trusted_directory(resolved_source.parent)
    ):
        raise ValueError("quarantine source is not an exact trusted thread schedule")
    expected_archive = (
        transaction_root / resolved_source.relative_to(source_root.parent)
    ).resolve(strict=False)
    if resolved_archive != expected_archive:
        raise ValueError("quarantine archive does not match its source")
    if record["reason"] not in {"due", "malformed"}:
        raise ValueError("invalid quarantine reason")
    digest = record["sha256"]
    if not isinstance(digest, str) or len(digest) != 64:
        raise ValueError("invalid quarantine digest")
    return source, archive, digest


def _recover(transaction: pathlib.Path, threads_root: pathlib.Path) -> None:
    manifest = transaction / "manifest.json"
    if not _trusted_file(manifest):
        raise ValueError(f"untrusted quarantine manifest: {manifest}")
    try:
        payload = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"malformed quarantine manifest: {manifest}") from exc
    records = payload.get("entries") if isinstance(payload, dict) else None
    if not isinstance(records, list) or not records:
        raise ValueError(f"invalid quarantine manifest: {manifest}")
    for record in records:
        source, archive, digest = _validate_record(record, transaction, threads_root)
        source_exists = os.path.lexists(source)
        archive_exists = os.path.lexists(archive)
        if source_exists and archive_exists:
            raise ValueError(f"ambiguous quarantine record: {source}")
        if not source_exists and not archive_exists:
            raise ValueError(f"lost quarantine record: {source}")
        current = archive if archive_exists else source
        if (
            not _trusted_file(current)
            or hashlib.sha256(current.read_bytes()).hexdigest() != digest
        ):
            raise ValueError(f"quarantine digest mismatch: {current}")
        if source_exists:
            relative_parent = archive.parent.relative_to(transaction)
            parent = transaction
            for component in relative_parent.parts:
                child = parent / component
                if os.path.lexists(child):
                    if not _trusted_directory(child):
                        raise ValueError(
                            f"untrusted quarantine archive directory: {child}"
                        )
                else:
                    child.mkdir(mode=0o700)
                    child.chmod(0o700)
                    _fsync(child)
                    _fsync(parent)
                parent = child
            os.replace(source, archive)
            _fsync(source.parent)
            _fsync(archive.parent)
    payload["state"] = "complete"
    _durable_json(manifest, payload)


def migrate(
    threads_root: pathlib.Path,
    quarantine_root: pathlib.Path,
    first_install: bool,
) -> None:
    if os.path.lexists(quarantine_root):
        if not _trusted_directory(quarantine_root):
            raise ValueError(f"untrusted quarantine directory: {quarantine_root}")
        for transaction in sorted(
            quarantine_root.iterdir(), key=lambda item: item.name
        ):
            if not _trusted_directory(transaction):
                raise ValueError(f"untrusted quarantine transaction: {transaction}")
            manifest = transaction / "manifest.json"
            contents = list(transaction.iterdir())
            recoverable_preparation = (
                transaction.name.startswith(".legacy-schedules-preparing-")
                and not os.path.lexists(manifest)
                and all(
                    item.name.startswith(".manifest.json.")
                    and item.name.endswith(".tmp")
                    and _trusted_file(item)
                    for item in contents
                )
            )
            if recoverable_preparation:
                for item in contents:
                    item.unlink()
                transaction.rmdir()
                _fsync(quarantine_root)
                continue
            _recover(transaction, threads_root)
    if not first_install or not threads_root.exists():
        return
    now = dt.datetime.now(dt.timezone.utc)
    hazards: list[tuple[pathlib.Path, bytes, str]] = []
    for thread_dir in sorted(threads_root.iterdir(), key=lambda item: item.name):
        scheduled = thread_dir / "scheduled.json"
        if not os.path.lexists(scheduled):
            continue
        data = scheduled.read_bytes()
        reason: str | None = None
        try:
            entries = json.loads(data.decode("utf-8"))
            if not isinstance(entries, list):
                raise ValueError("schedule root is not a list")
            for entry in entries:
                if not isinstance(entry, dict) or not isinstance(
                    entry.get("wake_at"), str
                ):
                    raise ValueError("schedule entry is invalid")
                wake_at = dt.datetime.fromisoformat(
                    entry["wake_at"].replace("Z", "+00:00")
                )
                if wake_at.tzinfo is None:
                    wake_at = wake_at.replace(tzinfo=dt.timezone.utc)
                if wake_at <= now:
                    reason = "due"
        except (UnicodeError, ValueError, TypeError, json.JSONDecodeError):
            reason = "malformed"
        if reason is not None:
            hazards.append((scheduled, data, reason))
    if not hazards:
        return
    quarantine_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    quarantine_root.chmod(0o700)
    transaction = pathlib.Path(
        tempfile.mkdtemp(prefix=".legacy-schedules-preparing-", dir=quarantine_root)
    )
    transaction.chmod(0o700)
    records = []
    for source, data, reason in hazards:
        archive = transaction / source.relative_to(threads_root.parent)
        records.append(
            {
                "source": str(source),
                "archive": str(archive),
                "reason": reason,
                "sha256": hashlib.sha256(data).hexdigest(),
            }
        )
    _durable_json(
        transaction / "manifest.json",
        {"version": 1, "state": "prepared", "entries": records},
    )
    _fsync(quarantine_root)
    _recover(transaction, threads_root)


def write_plist(args: argparse.Namespace) -> None:
    destination = args.destination
    if os.path.lexists(destination):
        metadata = destination.lstat()
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid():
            raise ValueError(f"untrusted pending LaunchAgent: {destination}")
    job = {
        "Label": args.label,
        "ProgramArguments": [str(args.waker), "--fire"],
        "RunAtLoad": True,
        "StartInterval": args.interval,
        "StandardOutPath": str(args.stdout_log),
        "StandardErrorPath": str(args.stderr_log),
        "EnvironmentVariables": {
            "HOME": str(args.home),
            "PATH": args.path_value,
            "CONTINUATION_HARNESS_HOME": str(args.harness_home),
        },
    }
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=destination.parent,
            prefix=f".{destination.name}.",
            delete=False,
        ) as temporary:
            temporary_name = temporary.name
            os.fchmod(temporary.fileno(), 0o600)
            plistlib.dump(job, temporary, fmt=plistlib.FMT_XML, sort_keys=True)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_name, destination)
        temporary_name = None
        _fsync(destination.parent)
    finally:
        if temporary_name is not None:
            pathlib.Path(temporary_name).unlink(missing_ok=True)


def backup(source: pathlib.Path) -> pathlib.Path:
    metadata = source.lstat()
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid():
        raise ValueError("existing LaunchAgent is untrusted")
    descriptor, name = tempfile.mkstemp(
        dir=source.parent, prefix=f".{source.name}.backup."
    )
    with os.fdopen(descriptor, "wb") as destination:
        os.fchmod(destination.fileno(), stat.S_IMODE(metadata.st_mode))
        destination.write(source.read_bytes())
        destination.flush()
        os.fsync(destination.fileno())
    _fsync(source.parent)
    return pathlib.Path(name)


def promote(source: pathlib.Path, destination: pathlib.Path) -> None:
    os.replace(source, destination)
    _fsync(destination.parent)


def lock_is_held(path: pathlib.Path, descriptor: int) -> bool:
    """Validate or acquire the shared lock through an inherited descriptor."""
    try:
        path_metadata = path.lstat()
        descriptor_metadata = os.fstat(descriptor)
        if (
            not _trusted_directory(path.parent)
            or not stat.S_ISREG(path_metadata.st_mode)
            or path_metadata.st_uid != os.getuid()
            or stat.S_IMODE(path_metadata.st_mode) & 0o022 != 0
            or (path_metadata.st_dev, path_metadata.st_ino)
            != (descriptor_metadata.st_dev, descriptor_metadata.st_ino)
        ):
            return False
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        os.set_inheritable(descriptor, True)
    except (OSError, ValueError):
        return False
    return True


def run_with_lock(path: pathlib.Path, command: list[str]) -> int:
    if not command:
        raise ValueError("lock-run requires a command")
    path.parent.mkdir(parents=True, exist_ok=True)
    if not _trusted_directory(path.parent):
        raise ValueError("supervisor lifecycle lock parent is untrusted")
    descriptor = os.open(
        path,
        os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid():
            raise ValueError("supervisor lifecycle lock is not owner-controlled")
        os.fchmod(descriptor, 0o600)
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        os.set_inheritable(descriptor, True)
        environment = dict(os.environ)
        environment["ESCAPEMENT_SUPERVISOR_LOCK_FD"] = str(descriptor)
        return subprocess.run(
            command,
            env=environment,
            pass_fds=(descriptor,),
            check=False,
        ).returncode
    finally:
        os.close(descriptor)


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    validate = subparsers.add_parser("validate")
    validate.add_argument("--threads", type=pathlib.Path, required=True)
    validate.add_argument("--quarantine", type=pathlib.Path, required=True)
    marker = subparsers.add_parser("marker-status")
    marker.add_argument("--path", type=pathlib.Path, required=True)
    marker.add_argument("--label", required=True)
    migration = subparsers.add_parser("migrate")
    migration.add_argument("--threads", type=pathlib.Path, required=True)
    migration.add_argument("--quarantine", type=pathlib.Path, required=True)
    migration.add_argument("--first-install", action="store_true")
    plist = subparsers.add_parser("write-plist")
    plist.add_argument("--destination", type=pathlib.Path, required=True)
    plist.add_argument("--label", required=True)
    plist.add_argument("--waker", type=pathlib.Path, required=True)
    plist.add_argument("--home", type=pathlib.Path, required=True)
    plist.add_argument("--path-value", required=True)
    plist.add_argument("--harness-home", type=pathlib.Path, required=True)
    plist.add_argument("--stdout-log", type=pathlib.Path, required=True)
    plist.add_argument("--stderr-log", type=pathlib.Path, required=True)
    plist.add_argument("--interval", type=int, required=True)
    backup_parser = subparsers.add_parser("backup")
    backup_parser.add_argument("--source", type=pathlib.Path, required=True)
    promotion = subparsers.add_parser("promote")
    promotion.add_argument("--source", type=pathlib.Path, required=True)
    promotion.add_argument("--destination", type=pathlib.Path, required=True)
    fsync_parent = subparsers.add_parser("fsync-parent")
    fsync_parent.add_argument("--path", type=pathlib.Path, required=True)
    marker_write = subparsers.add_parser("write-marker")
    marker_write.add_argument("--path", type=pathlib.Path, required=True)
    marker_write.add_argument("--label", required=True)
    trusted_file = subparsers.add_parser("validate-file")
    trusted_file.add_argument("--path", type=pathlib.Path, required=True)
    held_lock = subparsers.add_parser("lock-held")
    held_lock.add_argument("--path", type=pathlib.Path, required=True)
    held_lock.add_argument("--fd", type=int, required=True)
    lock_runner = subparsers.add_parser("lock-run")
    lock_runner.add_argument("--path", type=pathlib.Path, required=True)
    lock_runner.add_argument("argv", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if args.command == "validate":
        validate_state(args.threads, args.quarantine)
    elif args.command == "marker-status":
        print(marker_status(args.path, args.label))
    elif args.command == "migrate":
        migrate(args.threads, args.quarantine, args.first_install)
    elif args.command == "write-plist":
        write_plist(args)
    elif args.command == "backup":
        print(backup(args.source))
    elif args.command == "promote":
        promote(args.source, args.destination)
    elif args.command == "fsync-parent":
        _fsync(args.path.parent)
    elif args.command == "validate-file":
        if not _trusted_file(args.path):
            raise ValueError(f"untrusted file: {args.path}")
    elif args.command == "lock-held":
        return 0 if lock_is_held(args.path, args.fd) else 1
    elif args.command == "lock-run":
        return run_with_lock(args.path, args.argv)
    else:
        args.path.parent.mkdir(parents=True, exist_ok=True)
        _durable_json(args.path, {"label": args.label, "version": 1})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
