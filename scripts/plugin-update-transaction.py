#!/usr/bin/env python3
"""Durable rollback boundary for the Claude plugin cutover."""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import shutil
import stat
import subprocess
import tempfile


def _fsync_directory(path: pathlib.Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _durable_json(path: pathlib.Path, value: object, mode: int = 0o600) -> None:
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
            os.fchmod(temporary.fileno(), mode)
            json.dump(value, temporary, sort_keys=True, separators=(",", ":"))
            temporary.write("\n")
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_name, path)
        temporary_name = None
        _fsync_directory(path.parent)
    finally:
        if temporary_name is not None:
            pathlib.Path(temporary_name).unlink(missing_ok=True)


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


def _canonical_authority(path: pathlib.Path) -> pathlib.Path:
    """Canonicalize an authority's parent without following the authority itself."""
    return path.parent.resolve(strict=False) / path.name


def _copy_durable(source: pathlib.Path, destination: pathlib.Path) -> None:
    data = source.read_bytes()
    source_mode = stat.S_IMODE(source.stat().st_mode)
    temporary_name: str | None = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            dir=destination.parent, prefix=f".{destination.name}.", suffix=".tmp"
        )
        with os.fdopen(descriptor, "wb") as temporary:
            os.fchmod(temporary.fileno(), source_mode)
            temporary.write(data)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_name, destination)
        temporary_name = None
        _fsync_directory(destination.parent)
    finally:
        if temporary_name is not None:
            pathlib.Path(temporary_name).unlink(missing_ok=True)


def _fsync_trusted_file(path: pathlib.Path) -> None:
    if not _trusted_file(path):
        raise ValueError(f"cutover authority is untrusted: {path}")
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _wrapper_record(kind: str, path: pathlib.Path) -> dict:
    if not os.path.lexists(path):
        return {"kind": kind, "path": str(path), "exists": False, "target": None}
    if not path.is_symlink():
        raise ValueError(f"cutover wrapper is not a symlink: {path}")
    return {
        "kind": kind,
        "path": str(path),
        "exists": True,
        "target": os.readlink(path),
    }


def _file_record(
    kind: str,
    source: pathlib.Path,
    destination: pathlib.Path,
    *,
    required: bool,
) -> dict:
    if not os.path.lexists(source):
        if required:
            raise ValueError(f"required cutover authority is missing: {source}")
        return {
            "kind": kind,
            "source": str(source),
            "backup": None,
            "exists": False,
        }
    if not _trusted_file(source):
        raise ValueError(f"cutover authority is untrusted: {source}")
    _copy_durable(source, destination)
    return {
        "kind": kind,
        "source": str(source),
        "backup": str(destination),
        "exists": True,
    }


def _validate_file_record(
    record: object,
    *,
    kind: str,
    source: pathlib.Path,
    backup: pathlib.Path,
) -> None:
    if not isinstance(record, dict) or set(record) != {
        "kind",
        "source",
        "backup",
        "exists",
    }:
        raise ValueError("invalid cutover file record")
    if record["kind"] != kind or pathlib.Path(record["source"]) != source:
        raise ValueError("cutover file record escapes its authority")
    exists = record["exists"]
    if not isinstance(exists, bool):
        raise ValueError("invalid cutover file existence record")
    if exists:
        if pathlib.Path(record["backup"] or "") != backup or not _trusted_file(backup):
            raise ValueError("cutover file backup is invalid")
    elif record["backup"] is not None:
        raise ValueError("absent cutover file has a backup")


def _validate_state(
    state: object,
    *,
    journal_parent: pathlib.Path,
    backup: pathlib.Path,
) -> dict:
    if not isinstance(state, dict) or set(state) != {
        "files",
        "wrappers",
        "mode_root",
        "modes",
        "supervisor",
    }:
        raise ValueError("cutover backup state is invalid")
    home = journal_parent.parent
    expected_files = {
        "settings": (
            journal_parent / "settings.json",
            backup / "settings.json",
        ),
        "registry": (
            journal_parent / "plugins" / "installed_plugins.json",
            backup / "installed_plugins.json",
        ),
    }
    files = state["files"]
    if not isinstance(files, list) or len(files) != len(expected_files):
        raise ValueError("cutover file set is invalid")
    by_kind = {
        record.get("kind"): record for record in files if isinstance(record, dict)
    }
    if set(by_kind) != set(expected_files):
        raise ValueError("cutover file kinds are invalid")
    for kind, (source, saved) in expected_files.items():
        _validate_file_record(by_kind[kind], kind=kind, source=source, backup=saved)

    wrappers = state["wrappers"]
    expected_wrappers = {
        "bin": journal_parent / "harness" / "bin",
        "schemas": journal_parent / "harness" / "schemas",
    }
    if not isinstance(wrappers, list) or len(wrappers) != 2:
        raise ValueError("cutover wrapper set is invalid")
    wrapper_by_kind = {
        record.get("kind"): record for record in wrappers if isinstance(record, dict)
    }
    if set(wrapper_by_kind) != set(expected_wrappers):
        raise ValueError("cutover wrapper kinds are invalid")
    for kind, path in expected_wrappers.items():
        record = wrapper_by_kind[kind]
        if set(record) != {"kind", "path", "exists", "target"}:
            raise ValueError("invalid wrapper rollback record")
        if pathlib.Path(record["path"]) != path or not isinstance(
            record["exists"], bool
        ):
            raise ValueError("wrapper rollback record escapes its authority")
        target = record["target"]
        if record["exists"] != (isinstance(target, str) and bool(target)):
            raise ValueError("wrapper rollback target is invalid")

    supervisor = state["supervisor"]
    if not isinstance(supervisor, dict) or set(supervisor) != {
        "loaded",
        "marker",
        "plist",
    }:
        raise ValueError("cutover supervisor state is invalid")
    if not isinstance(supervisor["loaded"], bool):
        raise ValueError("cutover supervisor runtime state is invalid")
    _validate_file_record(
        supervisor["marker"],
        kind="supervisor_marker",
        source=journal_parent / "harness" / "continuation-supervisor-installed.json",
        backup=backup / "continuation-supervisor-installed.json",
    )
    _validate_file_record(
        supervisor["plist"],
        kind="supervisor_plist",
        source=home
        / "Library"
        / "LaunchAgents"
        / "com.escapement.continuation-supervisor.plist",
        backup=backup / "continuation-supervisor.plist",
    )

    modes = state["modes"]
    mode_root_raw = state["mode_root"]
    if not isinstance(modes, list):
        raise ValueError("cutover mode records are invalid")
    if mode_root_raw is None:
        if modes:
            raise ValueError("cutover modes have no authority root")
    else:
        serialized_root = pathlib.Path(mode_root_raw)
        mode_root = serialized_root.resolve(strict=True)
        cache_root = (
            journal_parent / "plugins" / "cache" / "escapement" / "escapement"
        ).resolve(strict=True)
        if serialized_root != mode_root:
            raise ValueError("cutover mode root is not canonical")
        try:
            mode_root.relative_to(cache_root)
        except ValueError as exc:
            raise ValueError("cutover mode root escapes plugin cache") from exc
        if mode_root.parts[-2:] != ("harness", "bin") or not _trusted_directory(
            mode_root
        ):
            raise ValueError("cutover mode root is invalid")
        for record in modes:
            if not isinstance(record, dict) or set(record) != {"path", "mode"}:
                raise ValueError("invalid cutover mode record")
            path = pathlib.Path(record["path"])
            mode = record["mode"]
            canonical_path = path.resolve(strict=True)
            if (
                path != canonical_path
                or canonical_path.parent != mode_root
                or not isinstance(mode, int)
            ):
                raise ValueError("cutover mode record escapes its authority")
    return state


def _load_journal(journal: pathlib.Path) -> tuple[pathlib.Path, pathlib.Path, dict]:
    if not _trusted_file(journal):
        raise ValueError(f"untrusted cutover journal: {journal}")
    try:
        pointer = json.loads(journal.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"malformed cutover journal: {journal}") from exc
    backup = (
        pathlib.Path(pointer.get("backup", ""))
        if isinstance(pointer, dict)
        else pathlib.Path()
    )
    try:
        journal_parent = journal.parent.resolve(strict=True)
        backup_resolved = backup.resolve(strict=True)
    except OSError as exc:
        raise ValueError("cutover journal backup cannot be resolved") from exc
    if (
        not backup.is_absolute()
        or backup_resolved.parent != journal_parent
        or not backup_resolved.name.startswith(".cutover-backup-")
        or not _trusted_directory(backup_resolved)
    ):
        raise ValueError("cutover journal points to an untrusted backup")
    backup = backup_resolved
    state_path = backup / "state.json"
    if not _trusted_file(state_path):
        raise ValueError("cutover backup state is missing or untrusted")
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("cutover backup state is malformed") from exc
    return (
        backup,
        state_path,
        _validate_state(state, journal_parent=journal_parent, backup=backup),
    )


def _guard_path(journal: pathlib.Path) -> pathlib.Path:
    return journal.with_name(f"{journal.name}.commit-guard")


def _prepare_rollback_journal(journal: pathlib.Path, *, cleanup_committed: bool) -> str:
    """Return the durable cutover outcome and prepare any requested recovery."""
    guard = _guard_path(journal)
    if os.path.lexists(journal):
        if os.path.lexists(guard):
            if not _trusted_file(guard) or journal.read_bytes() != guard.read_bytes():
                raise ValueError("cutover journal and guard disagree")
            guard.unlink()
            _fsync_directory(journal.parent)
        return "rollback"
    if not os.path.lexists(guard):
        return "rollback"
    if not _trusted_file(guard):
        raise ValueError("cutover commit guard is untrusted")
    try:
        pointer = json.loads(guard.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("cutover commit guard is malformed") from exc
    if isinstance(pointer, dict) and pointer.get("committed") is True:
        backup = pathlib.Path(pointer.get("backup", ""))
        backup = backup.resolve(strict=False)
        if (
            not backup.is_absolute()
            or backup.parent != journal.parent.resolve(strict=True)
            or not backup.name.startswith(".cutover-backup-")
        ):
            raise ValueError("committed cutover guard escapes its authority")
        if cleanup_committed:
            if backup.is_dir():
                if not _trusted_directory(backup):
                    raise ValueError("committed cutover backup is untrusted")
                shutil.rmtree(backup)
                _fsync_directory(backup.parent)
            guard.unlink()
            _fsync_directory(guard.parent)
        return "committed"
    os.replace(guard, journal)
    _fsync_directory(journal.parent)
    return "rollback"


def validate_authorities(paths: list[pathlib.Path]) -> None:
    for path in paths:
        if not _trusted_file(path):
            raise ValueError(f"plugin authority file is untrusted: {path}")


def begin(
    journal: pathlib.Path,
    settings: pathlib.Path,
    registry: pathlib.Path,
    wrappers: list[pathlib.Path],
    supervisor_marker: pathlib.Path,
    supervisor_plist: pathlib.Path,
    supervisor_loaded: bool,
) -> None:
    if os.path.lexists(journal) or os.path.lexists(_guard_path(journal)):
        raise ValueError(f"cutover journal already exists: {journal}")
    journal.parent.mkdir(parents=True, exist_ok=True)
    journal = _canonical_authority(journal)
    settings = _canonical_authority(settings)
    registry = _canonical_authority(registry)
    wrappers = [_canonical_authority(path) for path in wrappers]
    supervisor_marker = _canonical_authority(supervisor_marker)
    supervisor_plist = _canonical_authority(supervisor_plist)
    validate_authorities([settings, registry])
    backup = pathlib.Path(
        tempfile.mkdtemp(prefix=".cutover-backup-", dir=journal.parent)
    ).resolve(strict=True)
    backup.chmod(0o700)
    files = [
        _file_record("settings", settings, backup / "settings.json", required=True),
        _file_record(
            "registry",
            registry,
            backup / "installed_plugins.json",
            required=True,
        ),
    ]
    if len(wrappers) != 2:
        raise ValueError("plugin cutover requires bin and schemas wrappers")
    state = {
        "files": files,
        "wrappers": [
            _wrapper_record(kind, path)
            for kind, path in zip(("bin", "schemas"), wrappers)
        ],
        "mode_root": None,
        "modes": [],
        "supervisor": {
            "loaded": supervisor_loaded,
            "marker": _file_record(
                "supervisor_marker",
                supervisor_marker,
                backup / "continuation-supervisor-installed.json",
                required=False,
            ),
            "plist": _file_record(
                "supervisor_plist",
                supervisor_plist,
                backup / "continuation-supervisor.plist",
                required=False,
            ),
        },
    }
    _durable_json(backup / "state.json", state)
    _fsync_directory(backup)
    _durable_json(journal, {"backup": str(backup)})


def record_modes(journal: pathlib.Path, root: pathlib.Path) -> None:
    _, state_path, state = _load_journal(journal)
    root = root.resolve(strict=True)
    state["mode_root"] = str(root)
    state["modes"] = [
        {"path": str(path), "mode": stat.S_IMODE(path.stat().st_mode)}
        for path in sorted(root.iterdir(), key=lambda item: item.name)
        if path.is_file() and not path.is_symlink()
    ]
    _durable_json(state_path, state)


def _restore_wrapper(record: dict) -> None:
    path = pathlib.Path(record.get("path", ""))
    exists = record.get("exists")
    target = record.get("target")
    if not path.is_absolute() or not isinstance(exists, bool):
        raise ValueError("invalid wrapper rollback record")
    if not exists:
        path.unlink(missing_ok=True)
        _fsync_directory(path.parent)
        return
    if not isinstance(target, str) or not target:
        raise ValueError("invalid wrapper rollback target")
    staging = pathlib.Path(tempfile.mkdtemp(prefix=".rollback-link-", dir=path.parent))
    temporary = staging / "link"
    try:
        os.symlink(target, temporary)
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)
        staging.rmdir()


def _restore_file(record: dict) -> None:
    source = pathlib.Path(record["source"])
    if record["exists"]:
        _copy_durable(pathlib.Path(record["backup"]), source)
        return
    existed = os.path.lexists(source)
    source.unlink(missing_ok=True)
    if existed:
        _fsync_directory(source.parent)


def _run_supervisor_installer(
    supervisor_installer: pathlib.Path, argument: str
) -> subprocess.CompletedProcess:
    options: dict[str, object] = {"check": False}
    inherited = os.environ.get("ESCAPEMENT_SUPERVISOR_LOCK_FD")
    if inherited is not None:
        try:
            descriptor = int(inherited)
            os.fstat(descriptor)
        except (OSError, ValueError):
            pass
        else:
            options["pass_fds"] = (descriptor,)
    return subprocess.run([str(supervisor_installer), argument], **options)


def rollback(
    journal: pathlib.Path,
    supervisor_installer: pathlib.Path,
    *,
    cleanup_committed: bool,
) -> str:
    outcome = _prepare_rollback_journal(journal, cleanup_committed=cleanup_committed)
    if outcome == "committed":
        return outcome
    backup, _, state = _load_journal(journal)
    if not _trusted_file(supervisor_installer) or not os.access(
        supervisor_installer, os.X_OK
    ):
        raise ValueError("continuation supervisor installer is untrusted")
    quiesced = _run_supervisor_installer(supervisor_installer, "--quiesce")
    if quiesced.returncode != 0:
        raise RuntimeError("current continuation supervisor could not be quiesced")
    for record in state["files"]:
        _restore_file(record)
    for record in state["wrappers"]:
        if not isinstance(record, dict):
            raise ValueError("invalid wrapper rollback record")
        _restore_wrapper(record)
    for record in state["modes"]:
        path = pathlib.Path(record.get("path", ""))
        mode = record.get("mode")
        if path.is_file() and not path.is_symlink() and isinstance(mode, int):
            path.chmod(mode)
            descriptor = os.open(path, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
    _restore_file(state["supervisor"]["marker"])
    _restore_file(state["supervisor"]["plist"])
    command = [
        str(supervisor_installer),
        "--restore-loaded" if state["supervisor"]["loaded"] else "--restore-unloaded",
    ]
    completed = _run_supervisor_installer(supervisor_installer, command[1])
    if completed.returncode != 0:
        raise RuntimeError("prior continuation supervisor state could not be restored")
    journal.unlink()
    _fsync_directory(journal.parent)
    shutil.rmtree(backup)
    return "restored"


def commit(journal: pathlib.Path) -> None:
    backup, _, state = _load_journal(journal)
    for record in state["files"]:
        path = pathlib.Path(record["source"])
        _fsync_trusted_file(path)
        _fsync_directory(path.parent)
    for record in state["modes"]:
        path = pathlib.Path(record["path"])
        _fsync_trusted_file(path)
    guard = _guard_path(journal)
    if os.path.lexists(guard):
        raise ValueError("cutover commit guard already exists")
    os.link(journal, guard, follow_symlinks=False)
    _fsync_directory(journal.parent)
    journal.unlink()
    _fsync_directory(journal.parent)
    _durable_json(guard, {"backup": str(backup), "committed": True})


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "command",
        choices=(
            "begin",
            "record-modes",
            "rollback",
            "recover",
            "commit",
            "validate-authority",
        ),
    )
    parser.add_argument("--journal", type=pathlib.Path, required=True)
    parser.add_argument("--settings", type=pathlib.Path)
    parser.add_argument("--registry", type=pathlib.Path)
    parser.add_argument("--wrapper", type=pathlib.Path, action="append", default=[])
    parser.add_argument("--root", type=pathlib.Path)
    parser.add_argument("--path", type=pathlib.Path, action="append", default=[])
    parser.add_argument("--supervisor-marker", type=pathlib.Path)
    parser.add_argument("--supervisor-plist", type=pathlib.Path)
    parser.add_argument("--supervisor-loaded", choices=("true", "false"))
    parser.add_argument("--supervisor-installer", type=pathlib.Path)
    args = parser.parse_args()
    if args.command == "begin":
        if (
            args.settings is None
            or args.registry is None
            or args.supervisor_marker is None
            or args.supervisor_plist is None
            or args.supervisor_loaded is None
        ):
            parser.error("begin requires settings, registry, and supervisor snapshot")
        begin(
            args.journal,
            args.settings,
            args.registry,
            args.wrapper,
            args.supervisor_marker,
            args.supervisor_plist,
            args.supervisor_loaded == "true",
        )
    elif args.command == "record-modes":
        if args.root is None:
            parser.error("record-modes requires --root")
        record_modes(args.journal, args.root)
    elif args.command in {"rollback", "recover"}:
        if args.supervisor_installer is None:
            parser.error(f"{args.command} requires --supervisor-installer")
        outcome = rollback(
            args.journal,
            args.supervisor_installer,
            cleanup_committed=args.command == "recover",
        )
        if args.command == "rollback" and outcome == "committed":
            return 3
    elif args.command == "validate-authority":
        validate_authorities(args.path)
    else:
        commit(args.journal)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
