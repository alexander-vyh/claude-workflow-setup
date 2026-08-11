"""LIVE prompt-accuracy oracle for the wind-down judge's _SYSTEM classifier.

WHY THIS EXISTS (the gap it closes)
-----------------------------------
test_winddown_judge.py and test_winddown_gate.py inject the transport (`post=`) and
mock the verdict, so they test the ROUTING (verdict -> block/allow) but NEVER the
PROMPT. A prompt can pass every mocked test and still misclassify real messages — which
is exactly what happened: the narrow wrap-only prompt classified a real permission-
solicitation ("Want me to dig up X and draft Y? I can do both now — you'd just review")
as not_winddown, so the Stop gate allowed it and the user got a do-nothing solicitation
after 90s of churn. No mocked test could catch that because none of them exercise the
model. This test does.

THE ORACLE
----------
Business invariant: a turn-final message that offers to do obvious in-scope, reversible
work and asks permission instead of doing it MUST classify as wind-down (so the gate
blocks and the agent proceeds); a message that reports progress or asks a GENUINE
decision the user must own MUST NOT (so the gate does not nag a legitimate turn).

Independent source of truth: the labeled fixture set (fixtures/winddown_labeled.json),
authored from the real failure plus adversarial negative controls, NOT from the prompt's
wording. `expect` is the GATE DECISION (block/allow) the user experiences — not the raw
3-way label — because for a non-punt both not_winddown AND unclear->None both mean allow.

Fragile implementation this rejects: the pre-fix wrap-only prompt. It classifies the
permission-punt fixtures (P1/P4/P5) as not_winddown -> allow, so this test FAILS against
it. It also rejects an over-broad prompt that flags genuine decisions (N1/N4) as
wind-down — those are the negative controls.

TRANSPORT vs CLASSIFICATION: the local model server intermittently returns an empty
completion (message with no `content`) -> the client fails open to None. None is a
TRANSPORT outcome, not a classification, so we retry (spaced) to obtain real verdicts and
assert only on what the model actually returned. A positive case that never yields a real
verdict is an infra outage (skip that case), not a prompt regression. A single real
not_winddown on a positive — or a single real winddown on a negative — is a hard failure.

This test is SKIPPED when the local judge is unreachable (it cannot run without the
model). That is legitimate environment-gating, not oracle suppression: when the model IS
up, every fixture is asserted.
"""
import json
import os
import pathlib
import sys
import time

import pytest

BIN = pathlib.Path(__file__).resolve().parent.parent / "bin"
HOOKS = pathlib.Path(__file__).resolve().parents[2] / "claude" / "hooks"
for p in (str(BIN), str(HOOKS)):
    if p not in sys.path:
        sys.path.insert(0, p)

import winddown_judge as wj  # noqa: E402
import _local_judge_client as lj  # noqa: E402

FIXTURE = pathlib.Path(__file__).resolve().parent / "fixtures" / "winddown_labeled.json"
CASES = json.loads(FIXTURE.read_text())

# Spaced retries absorb the server's intermittent empty-content responses (a transport
# fail-open, tracked separately), without masking a real misclassification. A bounded
# per-call timeout keeps a pathological hang from eating the default 60s per attempt;
# on a healthy server each classification is ~3s.
_MAX_ATTEMPTS = 3
_SPACING_S = 1.0
os.environ.setdefault("ESCAPEMENT_LOCAL_JUDGE_TIMEOUT", "20")


def _judge_up() -> bool:
    """Two-endpoint reachability probe: BOTH /v1/models AND the chat endpoint listen.

    `/v1/models` answering 200 is necessary but NOT sufficient: the model-list endpoint
    can be up (HTTP 200) while the chat-completions endpoint is dead (connection refused
    / HTTP 000), in which case every `model_verdict` yields None and the positive-case
    assertion fails instead of skipping (escapement-pgo / u4a0 — observed 2026-07-07:
    /v1/models=200 while /v1/chat/completions=000).

    CRITICAL — this must NOT suppress a real oracle failure. The chat probe skips ONLY on
    a CONNECTION-DEAD signal (transport unreachable / non-2xx status). A chat endpoint that
    RESPONDS 200 — even with the intermittent empty-content fail-open the retry loop is
    designed to absorb — counts as UP, so a live model that misclassifies a fixture still
    reaches the assertion and still FAILS (never-suppress: an infra skip may only cover
    dead transport, never a wrong-but-live verdict). So we key on transport reachability,
    not on whether a generation produced content.
    """
    import json as _json
    import urllib.request
    base = lj.configured_base_url().rstrip("/")
    headers = {}
    authorization = lj.configured_auth_header()
    if authorization is not None:
        headers["authorization"] = authorization
    # 1) model-list must listen
    try:
        models_request = urllib.request.Request(base + "/models", headers=headers)
        with urllib.request.urlopen(models_request, timeout=5) as r:
            if r.status != 200:
                return False
    except Exception:
        return False
    # 2) chat endpoint must ALSO be reachable (transport check, minimal generation). A 2xx
    # of ANY body (including empty-content) => transport up => run the test. Only a
    # connection failure / non-2xx => skip. We do NOT inspect the completion content, so a
    # live-but-misclassifying model is never masked.
    try:
        req = urllib.request.Request(
            base + "/chat/completions",
            data=_json.dumps({
                "model": lj.DEFAULT_MODEL,
                "messages": [{"role": "user", "content": "ping"}],
                "max_tokens": 1,
            }).encode(),
            headers={"content-type": "application/json", **headers},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=15) as r:
            return 200 <= r.status < 300
    except urllib.error.HTTPError as e:
        # The server answered with a status — transport is UP. Treat as reachable so a
        # genuine model/prompt regression is not masked by a skip.
        return 200 <= e.code < 300
    except Exception:
        # Connection refused / timeout / DNS — transport genuinely dead => skip.
        return False


def _real_verdicts(text, want_at_least=2):
    """Sample model_verdict, spaced, keeping only NON-None (real) verdicts.

    Stops early once `want_at_least` real verdicts are collected. Returns the list of
    booleans the model actually produced (True=winddown, False=not_winddown); an empty
    list means the transport never yielded a verdict (infra outage for this case).
    """
    got = []
    for i in range(_MAX_ATTEMPTS):
        v = wj.model_verdict(text)
        if v is not None:
            got.append(v)
            if len(got) >= want_at_least:
                break
        if i < _MAX_ATTEMPTS - 1:
            time.sleep(_SPACING_S)
    return got


pytestmark = pytest.mark.skipif(
    not _judge_up(), reason="local judge (localhost:8000) unreachable or unauthenticated"
)


@pytest.mark.parametrize("case", [c for c in CASES if c["expect"] == "block"], ids=lambda c: c["id"])
def test_positive_cases_classify_as_winddown(case):
    """Permission-punt / wrap offers must be flagged winddown (True) whenever answered."""
    verdicts = _real_verdicts(case["text"])
    if not verdicts:
        pytest.skip("%s: judge transport yielded no verdict (server empty-content outage)" % case["id"])
    # Every real verdict must be winddown. A single real not_winddown is the regression
    # (the wrap-only prompt would produce exactly that for the permission-punt cases).
    assert all(v is True for v in verdicts), (
        "%s (%s): expected winddown, got real verdicts %s — prompt does not catch this class"
        % (case["id"], case["class"], ["winddown" if v else "not_winddown" for v in verdicts])
    )
    # And the gate decision the user feels must be block (work remains -> not a legit stop).
    decision, _ = wj.decide(case["text"], reversible_work_remains=True, model_offer=verdicts[-1])
    assert decision == "block"


@pytest.mark.parametrize("case", [c for c in CASES if c["expect"] == "allow"], ids=lambda c: c["id"])
def test_negative_cases_are_not_flagged_winddown(case):
    """Genuine decisions / progress reports must NEVER be misflagged as a punt."""
    verdicts = _real_verdicts(case["text"])
    if not verdicts:
        pytest.skip("%s: judge transport yielded no verdict (server empty-content outage)" % case["id"])
    # No real verdict may be winddown — that would be the over-correction (nagging a
    # legitimate clarifying question). not_winddown (or None->allow) is correct.
    assert all(v is False for v in verdicts), (
        "%s (%s): must NOT be winddown, got real verdicts %s — prompt over-flags genuine decisions"
        % (case["id"], case["class"], ["winddown" if v else "not_winddown" for v in verdicts])
    )
    decision, _ = wj.decide(case["text"], reversible_work_remains=True, model_offer=verdicts[-1])
    assert decision == "allow"
