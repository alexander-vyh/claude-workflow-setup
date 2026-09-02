"""Tests for winddown_judge.py — the local-LLM judge is the SOLE classifier.

ARCHITECTURE CHANGE (user directive): the regex floor is KILLED. Wind-down
classification in the judge/rung path is semantic — the judge's verdict is the
only signal there. There is no regex fallback inside `decide(...)`, which resolves
the offer purely from `model_offer`:
  - model_offer True  → offer
  - model_offer False → not an offer
  - model_offer None  → judge unavailable/unclear → FAIL-OPEN to allow (semantic
    and the outage is SIGNALLED (gate-design Rule 2) at the hook layer.
The Stop hook has a separate high-confidence outage sentinel for known DWDEV-style
wind-down shapes; these tests cover the judge/rung contract, not that sentinel.
The HTTP call is injected (`post=`) so tests never depend on a running server.

These tests pin two load-bearing behaviors the live run can't guarantee on every
machine:
  - FAIL-OPEN: model down / error / garbage verdict → None → allow (no fabricated block).
  - JUDGE OWNS RECALL: every offer/non-offer classification comes from the verdict,
    not from any string pattern.
"""
import pathlib
import sys

BIN = pathlib.Path(__file__).resolve().parent.parent / "bin"
sys.path.insert(0, str(BIN))

import winddown_judge as wj  # noqa: E402


# ---------------------------------------------------------------------------
# model_verdict — parse + fail-open (injected transport)
# ---------------------------------------------------------------------------

def test_verdict_winddown_true():
    assert wj.model_verdict("x", post=lambda url, payload, timeout: "winddown") is True


def test_verdict_not_winddown_false():
    assert wj.model_verdict("x", post=lambda url, payload, timeout: "not_winddown") is False


def test_verdict_unclear_or_garbage_is_none():
    assert wj.model_verdict("x", post=lambda url, payload, timeout: "unclear") is None
    assert wj.model_verdict("x", post=lambda url, payload, timeout: "banana") is None


def test_verdict_fails_open_on_exception():
    def boom(url, payload, timeout):
        raise TimeoutError("server down")
    # FAIL-OPEN: a down/slow model must yield None (defer to floor), never raise.
    assert wj.model_verdict("x", post=boom) is None


def test_model_verdict_uses_configured_local_judge_contract(monkeypatch):
    monkeypatch.setenv("ESCAPEMENT_LOCAL_JUDGE_BASE_URL", "http://127.0.0.1:9555/v1/")
    monkeypatch.setenv("ESCAPEMENT_LOCAL_JUDGE_MODEL", "rapid-mlx-loaded")
    calls = []

    def post(url, payload, timeout):
        calls.append((url, payload, timeout))
        return "winddown"

    assert wj.model_verdict("wrap?", timeout=3, post=post) is True
    assert calls == [
        (
            "http://127.0.0.1:9555/v1/chat/completions",
            {
                "model": "rapid-mlx-loaded",
                "messages": [
                    {"role": "system", "content": wj._SYSTEM},
                    {"role": "user", "content": "wrap?"},
                ],
                "max_tokens": 32,
                "enable_thinking": False,
                "temperature": 0,
            },
            3,
        )
    ]


def test_model_verdict_judges_request_and_terminal_response_together():
    captured = []

    def post(url, payload, timeout):
        captured.append(payload["messages"][1]["content"])
        return "winddown"

    assert wj.model_verdict(
        "The remaining step is to wire the credential into the supervisor.",
        user_request="Fix the monitoring path end to end.",
        post=post,
    ) is True
    [trajectory] = captured
    assert "Fix the monitoring path end to end." in trajectory
    assert "The remaining step is to wire the credential" in trajectory


# ---------------------------------------------------------------------------
# decide — judge verdict is the SOLE classifier, gated by reversible work
#
# REPLACES the old "union of regex floor + model verdict" suite (and its
# PARAPHRASE_REGEX_MISSES floor-gap cases). Under judge-only there is no floor to
# have a gap. Per never-suppress, the replacement oracle is equal-or-STRONGER: it
# pins that the verdict ALONE decides inside the judge/rung path, and the fail-open
# semantics (judge None → allow even for an obvious wrap offer) are asserted directly.
# ---------------------------------------------------------------------------

# A phrase an old regex floor WOULD have caught. Under judge-only it must NOT be
# special-cased: its classification comes entirely from model_offer.
OBVIOUS_WRAP_OFFER = "want me to wrap for the night, or keep going?"
# A phrase no regex floor ever caught — the judge owns it identically.
PARAPHRASE_OFFER = "I think this is a natural stopping point for today."


def test_model_flags_offer_with_work_blocks():
    # POSITIVE CONTROL: judge says offer + work remains → block. Holds for BOTH a
    # phrase the old regex caught and a paraphrase it missed — the judge owns both.
    for text in (OBVIOUS_WRAP_OFFER, PARAPHRASE_OFFER):
        d, r = wj.decide(text, reversible_work_remains=True, model_offer=True)
        assert (d, r) == ("block", "winddown_offer_work_remains"), (
            f"judge-flagged offer with work must block: {text!r}; got {d}/{r}"
        )


def test_model_says_not_offer_allows():
    # NEGATIVE CONTROL: judge says not-offer → allow, regardless of the text.
    for text in (OBVIOUS_WRAP_OFFER, "Should I use Postgres or SQLite?"):
        d, r = wj.decide(text, reversible_work_remains=True, model_offer=False)
        assert (d, r) == ("allow", "no_winddown_offer"), (
            f"judge-cleared text must allow: {text!r}; got {d}/{r}"
        )


def test_judge_unavailable_allows_even_obvious_wrap():
    # THE LOAD-BEARING CHANGE (inverts the old fall-back-to-regex test): with the
    # regex floor removed, judge None means NO classifier fired → allow, even for
    # an obvious wrap offer. "Semantic or nothing." A union/regex-fallback impl
    # would (wrongly) still BLOCK this — that is exactly what this test rejects.
    d, r = wj.decide(OBVIOUS_WRAP_OFFER, reversible_work_remains=True, model_offer=None)
    assert (d, r) == ("allow", "no_winddown_offer"), (
        "judge-only: an unavailable judge must NOT be backstopped by a regex floor; "
        f"got {d}/{r} — a regex fallback survived the refactor"
    )


def test_work_remains_gate_prevents_overnag_even_when_model_flags():
    # No reversible work left → legitimate stop; do not nag even if the judge flags it.
    d, r = wj.decide(OBVIOUS_WRAP_OFFER, reversible_work_remains=False, model_offer=True)
    assert (d, r) == ("allow", "winddown_but_no_reversible_work")


def test_decide_does_not_consult_any_regex(monkeypatch):
    # ARCHITECTURE GUARD: decide() must not call into a regex floor. If
    # winddown_gate still exposes is_winddown_offer, decide must NOT use it — make
    # it explode if called, and confirm decide still works off model_offer alone.
    import winddown_judge as _wj
    if hasattr(_wj, "wg") and hasattr(_wj.wg, "is_winddown_offer"):
        def _boom(*a, **k):
            raise AssertionError("decide() must not consult the regex floor (judge-only)")
        monkeypatch.setattr(_wj.wg, "is_winddown_offer", _boom)
    d, r = _wj.decide(OBVIOUS_WRAP_OFFER, reversible_work_remains=True, model_offer=True)
    assert (d, r) == ("block", "winddown_offer_work_remains")
    d2, r2 = _wj.decide(OBVIOUS_WRAP_OFFER, reversible_work_remains=True, model_offer=None)
    assert (d2, r2) == ("allow", "no_winddown_offer")
