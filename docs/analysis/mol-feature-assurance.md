# mol-feature assurance: what the available evidence supports

**Decision:** retain the outcome-bearing skeleton, full-execution, and outcome-check
stages. Do not remove any other stage on the present evidence. Measure active effort
and independently found defects before making an ROI claim, and test a conditional
review/retro variant prospectively rather than calling historical timestamps a cost
study.

This is intentionally an analysis report, not a validator or a proposal for new
workflow machinery.

## Evidence and selection

The frozen population contains 16 `mol-feature` roots from the Dashboards and
Escapement repositories at 2026-07-08 06:27:33 UTC. The deterministic sample is the
four most recently created closed Dashboards roots and the two closed Escapement
roots. That yields six roots and 54 expected stage records (nine per root):

| Root | Repository | Formula revision | Usable outcome |
|---|---|---:|---|
| `cro-executive-dashboard-mol-0lf` | Dashboards | 3 | **Censored:** root is closed but ceremony-retro and outcome-check children remain open |
| `cro-executive-dashboard-mol-db6` | Dashboards | 3 | Complete |
| `cro-executive-dashboard-mol-e0p` | Dashboards | 4 | Complete; only revision-4 root |
| `cro-executive-dashboard-mol-x9t` | Dashboards | 3 | Complete |
| `escapement-mol-2x7` | Escapement | 3 | Complete |
| `escapement-mol-741` | Escapement | 3 | Complete |

The other ten population roots were not silently discarded: seven were open or
deferred at the freeze and three older closed Dashboards roots fell outside the
four-root recency quota. Raw rows and their SHA-256 digests are preserved under
[`mol-feature-assurance-evidence`](mol-feature-assurance-evidence/manifest.json).

Five roots use formula revision 3 and one uses revision 4. The report therefore does
not pool them to claim a revision effect. The revision-4 observation is descriptive
only; it cannot distinguish a formula change from repository, feature, operator, or
calendar effects.

## What was measured—and what was not

All stage children were created when their molecule was poured. A child’s
`created_at` to `closed_at` duration is therefore cumulative molecule age, not stage
cost. The least misleading latency view is the calendar interval from the previous
stage closure to the current stage closure (root creation to brainstorm closure for
the first stage):

| Stage | Complete / censored | Calendar transition min / median / max (minutes) | Active effort | Independent quality evidence |
|---|---:|---:|---|---|
| brainstorm | 6 / 0 | 1 / 5 / 271 | Unknown | None captured |
| discovery | 6 / 0 | 0 / 9 / 372 | Unknown | None captured |
| review-discovery | 6 / 0 | 12 / 31 / 162 | Unknown | None captured |
| work-breakdown | 6 / 0 | 0 / 1 / 202 | Unknown | None captured |
| execute-skeleton | 6 / 0 | 0 / 303 / 753 | Unknown | One replay-backed control, one root |
| review-skeleton | 6 / 0 | 137 / 529 / 5,261 | Unknown | None captured |
| execute-full | 6 / 0 | 0 / 0 / 309 | Unknown | One replay-backed control, same root |
| ceremony-retro | 5 / 1 | 0 / 9 / 252 | Unknown | None captured |
| outcome-check | 5 / 1 | 0 / 0 / 40 | Unknown | Outcome status absent for censored root |

These are observed operational intervals, not model work time. They include human
waiting, overnight gaps, external work, concurrency, and delayed status closure. A
zero-minute interval means adjacent issues closed within the same minute; it does
not mean the stage cost nothing.

Model tokens, tool turns, agent count, human interventions, gate interruptions, and
wait intervals were not available at the freeze. They remain **null**, not zero.
Consequently the data do not support token cost, active-effort, or break-even
estimates. The longest observed interval—review-skeleton—cannot honestly be called
the most expensive stage without active-time evidence.

## Quality and defect accounting

No independently rooted defect-observation ledger was available. The number of
independent defects found, duplicates, escapes, reopenings, and final-outcome misses
is therefore **unknown**, not zero. Child descriptions are workflow instructions;
counting words such as “failure,” “review,” or “concern” in that prose would invent
defects.

Two executable counterfactual receipts survive source readback, both for
`escapement-mol-2x7`:

1. `execute-skeleton` at commit `85707c7b7c83cf537267dd1e3bed84cfbd33788b`
   binds the positive and negative authorization contracts in
   `harness/tests/test_repo_outcome.py`.
2. `execute-full` at commit `4126382fa7bd9a3a6ef5752279d45abf16635dba`
   binds the positive and negative Stop-backstop contracts in
   `harness/tests/test_verification_automerge_pr.py`.

These are two distinct invariants—declared authorization and enforcement at Stop—so
they are not double-counted as repeated reports of one defect. They demonstrate
reusable assurance in one feature, but they do not establish a population-wide
defect yield for either stage and do not credit either review stage with finding the
work.

## Stage decisions

| Stage | Label | Evidence-bounded decision |
|---|---|---|
| brainstorm | **MEASURE** | No unique quality or active-effort observation. Do not keep or remove it on calendar age alone. |
| discovery | **MEASURE** | No independent defect or outcome attribution. Capture active effort and decisions that materially changed scope. |
| review-discovery | **MEASURE** | The six closure intervals are observable; marginal quality is not. Require a semantic defect ledger before an ROI decision. |
| work-breakdown | **MEASURE** | Near-adjacent closures in several roots make status timestamps especially weak as a cost proxy. |
| execute-skeleton | **KEEP** | The source-backed authorization counterfactual proves one distinct protected outcome. Generality still needs measurement. |
| review-skeleton | **PROSPECTIVE EXPERIMENT** | It has the largest median calendar gap and no attributed independent defects, making conditional execution worth testing—but not yet safe to remove. |
| execute-full | **KEEP** | The source-backed Stop-backstop counterfactual proves a second, distinct protected outcome. |
| ceremony-retro | **PROSPECTIVE EXPERIMENT** | Only five observations are complete and no quality artifact is captured. Test asynchronous/conditional use; do not treat the missing sixth result as zero benefit. |
| outcome-check | **KEEP** | It is the measurement boundary for the business outcome. Removing it would make the missing outcome problem worse, not make the workflow leaner. |

No stage earns **REMOVE**. The evidence required to support removal—measured marginal
cost plus a quality-preserving counterfactual—is absent.

## Bounded experiments, not unsupported policy changes

**Lean candidate (prospective only):** on a randomly assigned set of low-risk
features, make review-skeleton and ceremony-retro conditional while retaining
execute-skeleton, execute-full, and outcome-check. Before assignment, define low risk
using observable scope (no production data mutation, authorization change, migration,
or external irreversible action). Compare active minutes, human interventions,
unique independently verified defects, reopenings, and proof-of-delivery success.
Restore the stages immediately if the candidate has any high-severity escape or its
outcome success is worse. This is not yet proven quality-preserving; the experiment
is how to find out.

**Higher-assurance candidate (prospective only):** require an additional independent
review for features with one of those high-risk properties, then compare unique
defects and escaped severity against matched standard-flow work. Do not count agent
agreement or duplicate observations as benefit.

For either experiment, a stage breaks even only when the independently valued loss
or rework it prevents exceeds its measured active effort, human attention, and
induced waiting. None of those operands is available in this freeze, so no numeric
break-even threshold is reported.

## Lean source reconciliation

This single check reconciles the report’s population, sample, censoring, revision,
and counterfactual counts directly from the retained JSONL evidence:

```bash
jq -n --slurpfile p docs/analysis/mol-feature-assurance-evidence/population.jsonl --slurpfile s docs/analysis/mol-feature-assurance-evidence/stages.jsonl --slurpfile c docs/analysis/mol-feature-assurance-evidence/counterfactuals.jsonl '{population_rows:($p|length),stage_rows:($s|length),sampled_roots:([$s[].root_id]|unique|length),closed_stage_rows:([$s[]|select(.status=="closed" and .closed_at!=null)]|length),censored_stage_rows:([$s[]|select(.status!="closed" or .closed_at==null)]|length),formula_revisions:([$s[].formula_revision]|unique),counterfactual_receipts:($c|length)}'
```

Expected result: `16` population rows, `54` stage rows, `6` sampled roots, `52`
closed stage rows, `2` censored stage rows, formula revisions `[3,4]`, and `2`
counterfactual receipts.
