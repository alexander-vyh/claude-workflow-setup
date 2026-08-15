"""Oracle for repo_outcome.resolve() / authorizes_auto_merge() — the reader for
`.escapement/repo.json`, the per-project options manifest (repo-outcome-authorization).

Business invariant: a repo's auto-merge authorization is honored ONLY when it is
explicitly and validly declared. Absent, malformed, or invalid declarations resolve to
the CONSERVATIVE default (intended_outcome=pr-opened, auto_merge_on_green=False) — an
unconfigured repo must behave exactly as today. A missing or broken file must never be
treated as authorization to merge/deploy live (design anti-metric #2).

Oracle quality:
  - NEGATIVE CONTROLS:
      * absent file        -> not authorized, source=default-absent
      * malformed JSON     -> not authorized, source=default-malformed, warning set
      * invalid outcome    -> not authorized, source=default-invalid, warning set
      * inconsistent decl  -> auto_merge_on_green=true but intended_outcome=pr-opened
                              => authorizes_auto_merge() is False (consistency guard)
  - POSITIVE CONTROL: valid merged-and-deployed + auto_merge_on_green:true
                      -> authorized, source=declared (auth not accidentally dropped)
  - FRAGILE IMPL REJECTED: a reader that returns authorized whenever the
    `auto_merge_on_green` key is truthy — the malformed case, the invalid-outcome
    case, and the pr-opened+true consistency case must all fail it.
"""
import json
import pathlib
import subprocess
import sys

BIN = pathlib.Path(__file__).resolve().parent.parent / "bin"
sys.path.insert(0, str(BIN))

import repo_outcome as ro  # noqa: E402


def _write(tmp_path, obj):
    d = tmp_path / ".escapement"
    d.mkdir(parents=True, exist_ok=True)
    p = d / "repo.json"
    if isinstance(obj, str):
        p.write_text(obj)
    else:
        p.write_text(json.dumps(obj))
    return tmp_path


def _git(repo, *args):
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        text=True,
        capture_output=True,
    )


def _init_worktree_repo(tmp_path, main_declaration=None):
    """Create a real primary/main checkout plus a sibling stale worktree.

    The two checkouts deliberately carry different declarations. This is the
    real topology that exposed the bug: current-worktree lookup observes the
    stale branch, while repository policy lives on the primary default branch.
    """
    primary = tmp_path / "primary"
    sibling = tmp_path / "sibling"
    primary.mkdir()
    _git(primary, "init", "--initial-branch=main")
    _git(primary, "config", "user.email", "test@example.com")
    _git(primary, "config", "user.name", "Test User")
    (primary / "README.md").write_text("fixture\n", encoding="utf-8")
    if main_declaration is not None:
        _write(primary, main_declaration)
    _git(primary, "add", ".")
    _git(primary, "commit", "-m", "primary policy")
    origin = tmp_path / "origin.git"
    subprocess.run(
        ["git", "init", "--bare", "--initial-branch=main", str(origin)],
        check=True,
        text=True,
        capture_output=True,
    )
    _git(primary, "remote", "add", "origin", str(origin))
    _git(primary, "push", "-u", "origin", "main")
    _git(primary, "fetch", "origin")
    _git(primary, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
    _git(primary, "worktree", "add", "-b", "stale-policy", str(sibling))
    return primary, sibling


def _commit_policy(repo, declaration, message):
    _write(repo, declaration)
    _git(repo, "add", ".escapement/repo.json")
    _git(repo, "commit", "-m", message)
    return _git(repo, "rev-parse", "HEAD").stdout.strip()


# --- POSITIVE CONTROL ------------------------------------------------------

def test_valid_merged_and_deployed_authorizes(tmp_path):
    root = _write(tmp_path, {
        "intended_outcome": "merged-and-deployed",
        "auto_merge_on_green": True,
        "deploy": {"surface": "Cloud Run exec dashboard"},
        "confirm_class": [],
    })
    out = ro.resolve(root)
    assert out.source == "declared"
    assert out.intended_outcome == "merged-and-deployed"
    assert out.auto_merge_on_green is True
    assert out.warning is None
    assert ro.authorizes_auto_merge(out) is True
    assert out.deploy == {"surface": "Cloud Run exec dashboard"}


def test_valid_merged_level_also_authorizes(tmp_path):
    root = _write(tmp_path, {"intended_outcome": "merged", "auto_merge_on_green": True})
    assert ro.authorizes_auto_merge(ro.resolve(root)) is True


def test_primary_default_branch_policy_authorizes_stale_sibling_worktree(tmp_path):
    _primary, sibling = _init_worktree_repo(
        tmp_path,
        {"intended_outcome": "merged", "auto_merge_on_green": True},
    )
    _write(sibling, {"intended_outcome": "pr-opened", "auto_merge_on_green": False})
    _git(sibling, "add", ".escapement/repo.json")
    _git(sibling, "commit", "-m", "stale worktree policy")

    resolved = ro.resolve(sibling)

    assert resolved.source == "declared-default-branch"
    assert resolved.intended_outcome == "merged"
    assert resolved.auto_merge_on_green is True
    assert ro.authorizes_auto_merge(resolved) is True


def test_unconfigured_primary_denies_even_if_sibling_self_authorizes(tmp_path):
    _primary, sibling = _init_worktree_repo(tmp_path)
    _write(sibling, {"intended_outcome": "merged", "auto_merge_on_green": True})
    _git(sibling, "add", ".escapement/repo.json")
    _git(sibling, "commit", "-m", "unauthorized sibling policy")

    resolved = ro.resolve(sibling)

    assert resolved.source == "default-absent"
    assert ro.authorizes_auto_merge(resolved) is False


def test_explicit_live_default_commit_authorizes_despite_local_disagreement(tmp_path):
    primary, sibling = _init_worktree_repo(
        tmp_path,
        {
            "intended_outcome": "merged-and-deployed",
            "auto_merge_on_green": True,
            "deploy": {"surface": "captured-live-default"},
        },
    )
    captured_live_default = _git(primary, "rev-parse", "HEAD").stdout.strip()

    # Both possible local substitutes now deny. The captured authenticated
    # live-default commit remains the sole policy authority for this decision.
    _commit_policy(
        primary,
        {"intended_outcome": "pr-opened", "auto_merge_on_green": False},
        "advance local remote-tracking default to denying policy",
    )
    _git(primary, "push", "origin", "main")
    _commit_policy(
        sibling,
        {"intended_outcome": "pr-opened", "auto_merge_on_green": False},
        "make invoking worktree deny",
    )

    invoking_policy = json.loads(ro.declaration_path(sibling).read_text())
    assert invoking_policy["auto_merge_on_green"] is False
    assert ro.authorizes_auto_merge(ro.resolve(sibling)) is False

    resolved = ro.resolve_at_ref(sibling, captured_live_default)

    assert resolved.source == "declared-ref"
    assert resolved.intended_outcome == "merged-and-deployed"
    assert resolved.deploy == {"surface": "captured-live-default"}
    assert ro.authorizes_auto_merge(resolved) is True


def test_explicit_live_default_commit_denies_despite_local_self_authorization(tmp_path):
    primary, sibling = _init_worktree_repo(
        tmp_path,
        {"intended_outcome": "pr-opened", "auto_merge_on_green": False},
    )
    captured_live_default = _git(primary, "rev-parse", "HEAD").stdout.strip()

    # Both the invoking worktree and the later origin/HEAD target authorize.
    # Neither may replace the exact live-default commit captured by the caller.
    _commit_policy(
        primary,
        {
            "intended_outcome": "merged-and-deployed",
            "auto_merge_on_green": True,
            "deploy": {"surface": "later-local-default"},
        },
        "advance local remote-tracking default to authorizing policy",
    )
    _git(primary, "push", "origin", "main")
    _commit_policy(
        sibling,
        {
            "intended_outcome": "merged-and-deployed",
            "auto_merge_on_green": True,
            "deploy": {"surface": "invoking-worktree"},
        },
        "self authorize invoking worktree",
    )

    invoking_policy = json.loads(ro.declaration_path(sibling).read_text())
    assert invoking_policy["auto_merge_on_green"] is True
    assert ro.authorizes_auto_merge(ro.resolve(sibling)) is True

    resolved = ro.resolve_at_ref(sibling, captured_live_default)

    assert resolved.source == "declared-ref"
    assert resolved.intended_outcome == "pr-opened"
    assert resolved.deploy is None
    assert ro.authorizes_auto_merge(resolved) is False


# --- NEGATIVE CONTROL: absent (the conservative default = today's behavior) ---

def test_absent_file_is_conservative_default(tmp_path):
    out = ro.resolve(tmp_path)  # no .escapement/repo.json written
    assert out.source == "default-absent"
    assert out.intended_outcome == "pr-opened"
    assert out.auto_merge_on_green is False
    assert ro.authorizes_auto_merge(out) is False


# --- NEGATIVE CONTROL: malformed (fail-safe, never authorize on broken file) ---

def test_malformed_json_falls_back_with_warning(tmp_path):
    root = _write(tmp_path, "{ this is not valid json ")
    out = ro.resolve(root)
    assert out.source == "default-malformed"
    assert out.auto_merge_on_green is False
    assert out.warning is not None and out.warning != ""
    assert ro.authorizes_auto_merge(out) is False


def test_non_object_json_falls_back(tmp_path):
    root = _write(tmp_path, json.dumps(["not", "an", "object"]))
    out = ro.resolve(root)
    assert out.source == "default-malformed"
    assert ro.authorizes_auto_merge(out) is False


# --- NEGATIVE CONTROL: invalid intended_outcome value ---

def test_invalid_intended_outcome_falls_back_with_warning(tmp_path):
    root = _write(tmp_path, {"intended_outcome": "yolo", "auto_merge_on_green": True})
    out = ro.resolve(root)
    assert out.source == "default-invalid"
    assert out.warning is not None
    assert ro.authorizes_auto_merge(out) is False


# --- NEGATIVE CONTROL: consistency guard (the subtle one) ---

def test_auto_merge_true_but_outcome_only_pr_opened_does_not_authorize(tmp_path):
    # A repo may not auto-merge if its declared intended outcome is only pr-opened.
    # This is the consistency guard: authorization requires BOTH the flag AND an
    # outcome at or above 'merged'. A fragile reader keying only on the boolean fails here.
    root = _write(tmp_path, {"intended_outcome": "pr-opened", "auto_merge_on_green": True})
    out = ro.resolve(root)
    assert out.source == "declared"          # it IS a valid declaration
    assert out.auto_merge_on_green is True    # the field is faithfully read
    assert ro.authorizes_auto_merge(out) is False  # but it does not authorize


# --- ladder / helper sanity ---

def test_ladder_ordering_exposed(tmp_path):
    assert ro.INTENDED_OUTCOME_LADDER.index("committed") < \
           ro.INTENDED_OUTCOME_LADDER.index("pr-opened") < \
           ro.INTENDED_OUTCOME_LADDER.index("merged") < \
           ro.INTENDED_OUTCOME_LADDER.index("merged-and-deployed")
