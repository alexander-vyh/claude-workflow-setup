"""Tests for trusted_source — the flat-file command-source trust guard.

Business invariant: the harness must NOT shell-execute a command string sourced
from a file that another local user could have tampered with. "Tampered with"
means: the file (or the directory holding it) is owned by someone else, or is
writable by group/other. This is the StrictModes pattern (cf. ssh) applied to
the harness's auto-executing config (scheduled.json / contract.json).

Fragile implementation this rejects: an existence-only check. A guard that only
asks "does the file exist?" would happily run a command from a world-writable
file — the negative controls below (0o666 file, 0o777 parent) catch exactly that.
"""

import importlib.util
import os
import pathlib
import sys

import pytest

BIN = pathlib.Path(__file__).resolve().parent.parent / "bin"
sys.path.insert(0, str(BIN))


def _load_trusted_source():
    spec = importlib.util.spec_from_file_location(
        "trusted_source", BIN / "trusted_source.py"
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load harness/bin/trusted_source.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


ts = _load_trusted_source()

POSIX = hasattr(os, "geteuid")
skip_non_posix = pytest.mark.skipif(not POSIX, reason="perms/ownership are POSIX-only")


# --- positive control: a normal user-owned, tightly-permissioned file -------


@skip_non_posix
def test_user_owned_private_file_is_trusted(tmp_path):
    f = tmp_path / "scheduled.json"
    f.write_text("[]")
    f.chmod(0o600)
    assert ts.is_trusted_file(f) is True


@skip_non_posix
def test_group_other_readable_but_not_writable_is_trusted(tmp_path):
    # 0o644 / 0o755 dirs are the common default umask result — must stay usable.
    f = tmp_path / "scheduled.json"
    f.write_text("[]")
    f.chmod(0o644)
    assert ts.is_trusted_file(f) is True


# --- negative controls: the tamper surfaces ---------------------------------


def test_missing_file_is_untrusted(tmp_path):
    assert ts.is_trusted_file(tmp_path / "nope.json") is False


@skip_non_posix
def test_world_writable_file_is_untrusted(tmp_path):
    f = tmp_path / "scheduled.json"
    f.write_text("[]")
    f.chmod(0o666)  # group + other writable: anyone can rewrite the command
    assert ts.is_trusted_file(f) is False


@skip_non_posix
def test_group_writable_file_is_untrusted(tmp_path):
    f = tmp_path / "scheduled.json"
    f.write_text("[]")
    f.chmod(0o664)
    assert ts.is_trusted_file(f) is False


@skip_non_posix
def test_world_writable_parent_dir_is_untrusted(tmp_path):
    # Even a 0o600 file is forgeable if its directory is world-writable (no
    # sticky bit): an attacker replaces the file wholesale.
    d = tmp_path / "loose"
    d.mkdir()
    f = d / "scheduled.json"
    f.write_text("[]")
    f.chmod(0o600)
    d.chmod(0o777)  # world-writable, NOT sticky
    try:
        assert ts.is_trusted_file(f) is False
    finally:
        d.chmod(0o755)  # let pytest clean up


@skip_non_posix
def test_directory_is_not_a_trusted_file(tmp_path):
    assert ts.is_trusted_file(tmp_path) is False


# --- assert_trusted_file raises with an actionable message ------------------


@skip_non_posix
def test_assert_trusted_file_raises_on_untrusted(tmp_path):
    f = tmp_path / "scheduled.json"
    f.write_text("[]")
    f.chmod(0o666)
    with pytest.raises(ts.UntrustedSource):
        ts.assert_trusted_file(f)


@skip_non_posix
def test_assert_trusted_file_passes_on_trusted(tmp_path):
    f = tmp_path / "scheduled.json"
    f.write_text("[]")
    f.chmod(0o600)
    assert ts.assert_trusted_file(f) is None
