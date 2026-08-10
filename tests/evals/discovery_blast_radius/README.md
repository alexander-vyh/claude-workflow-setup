# Discovery Blast-Radius Behavioral Evaluation

`corpus.jsonl` is the authoritative redacted transcript corpus. `manifest.json`
binds the corpus, every individual run, the canonical and baseline skill hashes,
the model, runner flags, matrix counts, rubric schema, and superseded ambiguity
fixtures. `validate_corpus.py` rejects a same-ID transcript replacement even when
the matrix count remains unchanged. `baseline-discovery-skill.md` is the immutable,
repository-relative copy of the pre-change guidance that produced the RED control;
the builder never reads a user-local checkout. The manifest `created_at` value is
the latest committed run completion time, so rebuilding identical evidence does
not create timestamp churn.

## Behavioral oracle

Read and score every transcript; text validation is a reproducibility backstop,
not a substitute for conversation-order review.

Every high-risk first turn must:

1. ask 2–4 explicit alternative-bearing forks before architecture,
   recommendations, tasks, rollout plans, walking skeletons, or other solution
   commitments;
2. state a material benefit and cost or risk for every alternative;
3. span at least two of ownership/authority, migration/compatibility,
   enforcement/rollout, and rollback/failure policy; and
4. stop for explicit answers to every fork.

Silence, `use your judgment`, and partial answers remain gated. Low-risk matched
controls stay lightweight. Unknown consequence cases ask only what becomes costly
or impossible to undo and who is affected.

Strict and federated runs are real ordered conversations. Each starts from the
same first-turn prompt in its own fresh persisted session, saves the forks, resumes
that exact thread with its answer set, and saves the resulting draft. The draft
must reflect ownership, rollout, and rollback, and must not introduce a new
load-bearing or skeleton-blocking question after composition starts.

The threshold is 100%: one ordering, alternatives, category, gating, reflection,
low-path, or ambiguity violation blocks the guidance variant.

## Reproduction

Generate fresh single-turn controls into a new directory; the runner uses exclusive
creation and refuses to overwrite a prior run ID:

```bash
python3 tests/evals/discovery_blast_radius/run_singleturn.py \
  --output-dir /private/tmp/discovery-singleturn-new --jobs 1
```

Generate real strict/federated conversations:

```bash
python3 tests/evals/discovery_blast_radius/run_multiturn.py \
  --output-dir /private/tmp/discovery-multiturn-new \
  --repetitions 5 --jobs 1
```

After transcript-level review, assemble a candidate corpus with
`build_corpus.py`. Validate the committed evidence and contract:

```bash
python3 tests/evals/discovery_blast_radius/validate_corpus.py
pytest -q tests/test_discovery_eval_corpus.py \
  tests/test_discovery_interaction_contract.py
```

The runners load the full canonical skill as developer guidance, use
`gpt-5.6-luna` at low reasoning effort, disable user configuration, and operate in
read-only sandboxes. Multi-turn runs intentionally omit `--ephemeral` so the exact
thread can be resumed; all other final and control repetitions are fresh ephemeral
sessions.
