"""Real-Git fixtures shared by oracle-downgrade public-hook tests."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
LANDING_HOOKS = (
    ROOT / "claude" / "hooks" / "oracle_downgrade_warning_gate.py",
    ROOT
    / "plugins"
    / "escapement"
    / "claude"
    / "hooks"
    / "oracle_downgrade_warning_gate.py",
    ROOT
    / "plugins"
    / "escapement-claude"
    / "hooks"
    / "oracle_downgrade_warning_gate.py",
)
STOP_HOOKS = (
    ROOT / "claude" / "hooks" / "oracle_downgrade_stop.py",
    ROOT / "plugins" / "escapement-claude" / "hooks" / "oracle_downgrade_stop.py",
)
PUBLIC_HOOKS = tuple((hook, "PreToolUse") for hook in LANDING_HOOKS) + tuple(
    (hook, "Stop") for hook in STOP_HOOKS
)

STRONG = (
    "def test_total():\n    assert compute() == 42\n    assert category() == 'active'\n"
)
WEAK = "def test_total():\n    assert compute()\n"
DUPLICATE_STRONG = (
    "def test_total():\n    assert compute() == 42\n    assert compute() == 42\n"
)
SINGLE_STRONG = "def test_total():\n    assert compute() == 42\n"


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def git_bytes(
    repo: Path,
    *args: str,
    input_bytes: bytes | None = None,
) -> bytes:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        input=input_bytes,
        check=True,
        capture_output=True,
    )
    return result.stdout


def write(repo: Path, relative: str, content: str) -> None:
    path = repo / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def commit(repo: Path, message: str) -> None:
    git(repo, "add", "--all")
    git(repo, "commit", "-m", message)


def landing_repo(tmp_path: Path, baseline_test: str) -> Path:
    origin = tmp_path / "origin.git"
    repo = tmp_path / "repo"
    subprocess.run(
        ["git", "init", "--bare", str(origin)], check=True, capture_output=True
    )
    subprocess.run(
        ["git", "clone", str(origin), str(repo)], check=True, capture_output=True
    )
    git(repo, "config", "user.email", "oracle@example.test")
    git(repo, "config", "user.name", "Oracle Test")
    git(repo, "checkout", "-b", "trunk")
    write(repo, "tests/test_total.py", baseline_test)
    write(repo, "README.md", "baseline\n")
    commit(repo, "landing baseline")
    git(repo, "push", "-u", "origin", "trunk")
    git(repo, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/trunk")
    return repo


def feature_repo(tmp_path: Path, baseline_test: str) -> Path:
    repo = landing_repo(tmp_path, baseline_test)
    git(repo, "checkout", "-b", "feature/oracle-change")
    return repo


def raw_object_repo(
    tmp_path: Path,
    candidate_sources: dict[bytes, str],
) -> tuple[Path, tuple[bytes, ...], str]:
    """Build strong/candidate commits at paths macOS cannot check out.

    Git object and index plumbing keeps raw paths byte-preserving. Candidate
    entries are loaded into the index with skip-worktree, so hook evaluation
    must use Git object/path semantics without a filesystem checkout.
    """
    repo = tmp_path / "raw-repo"
    subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
    git(repo, "config", "user.email", "oracle@example.test")
    git(repo, "config", "user.name", "Oracle Test")
    raw_names = tuple(sorted(candidate_sources))
    raw_paths = tuple(b"tests/" + name for name in raw_names)

    def tree_for(sources: dict[bytes, str]) -> str:
        tests_records = []
        for raw_name in raw_names:
            blob = git_bytes(
                repo,
                "hash-object",
                "-w",
                "--stdin",
                input_bytes=sources[raw_name].encode(),
            ).strip()
            tests_records.append(b"100644 blob " + blob + b"\t" + raw_name + b"\0")
        tests_tree = git_bytes(
            repo, "mktree", "-z", input_bytes=b"".join(tests_records)
        ).strip()
        root_record = b"040000 tree " + tests_tree + b"\ttests\0"
        return git_bytes(repo, "mktree", "-z", input_bytes=root_record).strip().decode()

    baseline_tree = tree_for(dict.fromkeys(raw_names, STRONG))
    candidate_tree = tree_for(candidate_sources)
    baseline = (
        git_bytes(repo, "commit-tree", baseline_tree, input_bytes=b"strong baseline\n")
        .strip()
        .decode()
    )
    candidate = (
        git_bytes(
            repo,
            "commit-tree",
            candidate_tree,
            "-p",
            baseline,
            input_bytes=b"weak candidate\n",
        )
        .strip()
        .decode()
    )

    git(repo, "update-ref", "refs/remotes/origin/trunk", baseline)
    git(repo, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/trunk")
    git(repo, "update-ref", "refs/heads/feature/oracle-change", candidate)
    git(repo, "symbolic-ref", "HEAD", "refs/heads/feature/oracle-change")
    git(repo, "read-tree", candidate)
    git_bytes(
        repo,
        "update-index",
        "--skip-worktree",
        "-z",
        "--stdin",
        input_bytes=b"\0".join(raw_paths) + b"\0",
    )
    assert git_bytes(repo, "status", "--porcelain=v1", "-z") == b""
    return repo, raw_paths, baseline


def raw_non_utf8_repo(tmp_path: Path) -> tuple[Path, bytes, str]:
    repo, paths, baseline = raw_object_repo(tmp_path, {b"test_non_utf8_\xff.py": WEAK})
    return repo, paths[0], baseline


def raw_rename_repo(
    tmp_path: Path,
    renames: tuple[tuple[bytes, bytes, str], ...],
) -> tuple[Path, tuple[tuple[bytes, bytes], ...], str]:
    """Build inexact raw-byte renames without checking illegal paths out."""
    repo = tmp_path / "raw-rename-repo"
    subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
    git(repo, "config", "user.email", "oracle@example.test")
    git(repo, "config", "user.name", "Oracle Test")

    baseline_sources: dict[bytes, str] = {}
    candidate_sources: dict[bytes, str] = {}
    raw_pairs = []
    for index, (old_name, new_name, candidate_source) in enumerate(renames):
        context = "".join(
            f"# retained raw rename {index} context {line:02d}\n" for line in range(40)
        )
        baseline_sources[old_name] = STRONG + context
        candidate_sources[new_name] = candidate_source + context
        raw_pairs.append((b"tests/" + old_name, b"tests/" + new_name))

    def tree_for(sources: dict[bytes, str]) -> str:
        tests_records = []
        for raw_name, source in sorted(sources.items()):
            blob = git_bytes(
                repo,
                "hash-object",
                "-w",
                "--stdin",
                input_bytes=source.encode(),
            ).strip()
            tests_records.append(b"100644 blob " + blob + b"\t" + raw_name + b"\0")
        tests_tree = git_bytes(
            repo, "mktree", "-z", input_bytes=b"".join(tests_records)
        ).strip()
        return (
            git_bytes(
                repo,
                "mktree",
                "-z",
                input_bytes=b"040000 tree " + tests_tree + b"\ttests\0",
            )
            .strip()
            .decode()
        )

    baseline = (
        git_bytes(
            repo,
            "commit-tree",
            tree_for(baseline_sources),
            input_bytes=b"raw rename baseline\n",
        )
        .strip()
        .decode()
    )
    candidate = (
        git_bytes(
            repo,
            "commit-tree",
            tree_for(candidate_sources),
            "-p",
            baseline,
            input_bytes=b"raw rename candidate\n",
        )
        .strip()
        .decode()
    )
    git(repo, "update-ref", "refs/remotes/origin/trunk", baseline)
    git(repo, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/trunk")
    git(repo, "update-ref", "refs/heads/feature/oracle-change", candidate)
    git(repo, "symbolic-ref", "HEAD", "refs/heads/feature/oracle-change")
    git(repo, "read-tree", candidate)
    candidate_paths = tuple(new_path for _old_path, new_path in raw_pairs)
    git_bytes(
        repo,
        "update-index",
        "--skip-worktree",
        "-z",
        "--stdin",
        input_bytes=b"\0".join(candidate_paths) + b"\0",
    )
    assert git_bytes(repo, "status", "--porcelain=v1", "-z") == b""
    return repo, tuple(raw_pairs), baseline


def edited_rename_repo(tmp_path: Path) -> tuple[Path, str, tuple[str, ...]]:
    """Create several inexact renames beyond an inherited rename limit."""
    repo = landing_repo(tmp_path, STRONG)
    old_paths = tuple(f"tests/test_old_{index}.py" for index in range(3))
    new_paths = tuple(f"tests/test_new_{index}.py" for index in range(3))
    for index, path in enumerate(old_paths):
        source = (
            f"def test_case_{index}():\n"
            f"    assert compute_{index}() == 42\n"
            f"    assert category_{index}() == 'active'\n"
            + "".join(f"# retained context {line}\n" for line in range(30))
        )
        write(repo, path, source)
    commit(repo, "add rename-limit baselines")
    git(repo, "push", "origin", "trunk")
    baseline = git(repo, "rev-parse", "refs/remotes/origin/trunk")
    git(repo, "checkout", "-b", "feature/oracle-change")
    for old_path, new_path in zip(old_paths, new_paths, strict=True):
        git(repo, "mv", old_path, new_path)
        with (repo / new_path).open("a", encoding="utf-8") as handle:
            handle.write("# edited after rename\n")
    commit(repo, "rename and edit several tests")
    git(repo, "config", "diff.renames", "false")
    git(repo, "config", "diff.renameLimit", "1")
    return repo, baseline, new_paths


def weakening_rename_repo(tmp_path: Path) -> tuple[Path, str, str]:
    """Create an inexact rename whose retained context hides a real weakening."""
    repo = landing_repo(tmp_path, STRONG)
    old_path = "tests/test_total.py"
    new_path = "tests/test_renamed_weaker.py"
    context = "".join(f"# retained rename context {line:02d}\n" for line in range(40))
    write(repo, old_path, STRONG + context)
    commit(repo, "add retained context to rename baseline")
    git(repo, "push", "origin", "trunk")
    baseline = git(repo, "rev-parse", "refs/remotes/origin/trunk")
    git(repo, "checkout", "-b", "feature/oracle-change")
    git(repo, "mv", old_path, new_path)
    write(repo, new_path, WEAK + context)
    commit(repo, "rename and weaken oracle")
    git(repo, "config", "diff.renames", "false")
    git(repo, "config", "diff.renameLimit", "1")
    return repo, baseline, new_path


def run_hook(
    hook: Path,
    repo: Path,
    event: str,
    *,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    if event == "Stop":
        payload = {"hook_event_name": "Stop", "cwd": str(repo)}
    else:
        payload = {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "cwd": str(repo),
            "tool_input": {"command": "gh pr create --title oracle-change"},
        }
    return subprocess.run(
        [sys.executable, str(hook)],
        cwd=repo,
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


def advisory_message(result: subprocess.CompletedProcess[str], event: str) -> str:
    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout)
    if event == "Stop":
        return output["systemMessage"]
    decision = output["hookSpecificOutput"]
    assert decision["permissionDecision"] == "ask"
    return decision["permissionDecisionReason"]
