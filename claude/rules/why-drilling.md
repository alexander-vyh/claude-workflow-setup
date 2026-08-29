# Why-Drilling — Depth-1 Probe (always-on)

When a **load-bearing why is thin**, drill it once before acting. A *why* is
load-bearing if a decision, design, or recommendation rests on it. It is **thin**
when it is (a) **authority-shaped** ("X asked for it", "it's a mandate",
"leadership wants it"), (b) **a premise the user asserted that no artifact has
confirmed**, or (c) **a reason that restates the request** instead of naming an
observable outcome.

## The Probe — one question, non-blocking

On a thin load-bearing why, run ONE inline check before proceeding:

> "Is the stated reason the real, observable outcome — or an unconfirmed inference?"

Bottom out at one of two terminals, then continue:

- **Confirmed observable outcome** — the why resolves to something checkable (a
  metric, a failing test, a reproduced symptom, a stated success criterion). Proceed.
- **Unconfirmed inference** — the why rests on a guess or a proxy (e.g. someone
  invoking an absent authority). **Mark it unconfirmed, name who/what would confirm
  it, and proceed — do not block.** Interactive: you may ask. Non-interactive:
  record the assumption and continue.

This is a **floor, not a ceiling.** One pass catches the *obvious* false premise.
Non-obvious framing errors need the deeper tiers (Drill = root-cause / Grill = full
interview) — opt-in and gated behind real agent fan-out, NOT this probe.

## Escape

A one-word user out — "just answer" / "skip" — suppresses the Probe for that turn.

## Signal

When you fire a Probe, append one record via `claude/hooks/_gate_signal.py` `record(...)`:
gate `why-drilling`, decision `probe-fired` | `probe-empty` | `escape-used`, with the why
excerpt. This is rule-based, not a hook, so it relies on compliance.
