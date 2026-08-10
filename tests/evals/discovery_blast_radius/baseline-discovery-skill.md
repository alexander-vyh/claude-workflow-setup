---
name: discovery
description: >
  Pre-design-doc discovery skill. Fills the gap between "we should build X"
  and "here is the design doc." Produces genuine thinking through adversarial
  questions, not checkbox compliance. Requires a confirmed problem framing as
  input (Input Gate) — then drafts solutions via a draft-and-react model.
  Uses OpenSpec as the structured engine underneath — output goes to
  openspec/changes/{name}/.
---

# Discovery Skill

## When to Use

Use `/discovery` when you have a problem or feature idea but no structured plan yet.
This skill produces a design document that is ready to execute — or reveals that you
need a spike first.

> **Before the Input Gate, in an unfamiliar domain:** discovery requires a confirmed
> problem framing to start — but if the domain is unfamiliar and a load-bearing
> distinction hinges on terminology you don't command, the framing itself may be
> wrong. Recover the field's vocabulary first via the **`vocab`** skill (`/vocab`);
> it runs *pre-framing* and feeds a real frame back in. Skip for familiar domains,
> codebase/org-internal questions, or one-fact lookups.

**This skill replaces `/opsx:propose`.** Both create OpenSpec changes, but `/discovery`
adds adversarial thinking, draft-and-react interaction, and schema-aware scaling. If
the project has an `openspec-propose` skill installed, `/discovery` supersedes it for
all design work. The generated `openspec-propose` skill is redundant when this skill
is active.

## Invocation

```
/discovery {topic}                    # Create mode, schema auto-detected
/discovery --schema rapid {topic}     # Create mode, force rapid schema
/discovery --schema epic {topic}      # Create mode, force epic schema
/discovery edit {change-name}         # Edit mode on existing change
```

Argument parsing:
- `--schema rapid|feature|epic` forces a specific ceremony level (optional — auto-detected if omitted)
- If the first non-flag argument is `edit`, enter Edit mode on the named change
- Otherwise, treat the remaining arguments as the topic name

## Initialization

On first use in any project, before any other step:

1. Check if the `openspec` binary is available (run `which openspec`)
2. If the binary is NOT available: warn the user once — "OpenSpec CLI not found.
   Install it to use structured discovery. Falling back to unstructured mode." Then
   produce the design as a single markdown file at `docs/plans/YYYY-MM-DD-{topic}-design.md`
   using the adversarial overlays from this skill, without any OpenSpec commands.
3. If the binary IS available but `openspec/` directory does not exist in the project root:
   tell the user: "This project doesn't have OpenSpec set up yet. I'll initialize it
   to manage design documents." Then run:
   ```bash
   openspec init --tools claude
   ```
   On success, continue silently. If `openspec init` fails (permissions, disk error,
   etc.), announce: "OpenSpec setup failed — falling back to docs/plans/ format for
   this session." Then proceed in Unstructured Fallback Mode.
   If directories need to be created (e.g., `openspec/changes/`), create them silently.

## Input Gate

Discovery designs **solutions**. It does not define problems. Before drafting any
solution artifact (`proposal.md`, `design.md`, `specs/`, `tasks.md`), discovery
requires a **confirmed problem framing** — the six fields below, each confirmed by
the user, not inferred by you.

**This gate is the precondition for the Draft-and-React model below.** Draft-and-
react is appropriate *because* the input is confirmed. Given an unconfirmed input,
draft-and-react fabricates — it fills framing gaps with plausible prose that reads
like a real plan. The gate is what makes draft-and-react safe.

**Scope:** this gate applies to `feature` and `epic` schemas. `rapid`-schema work
(bug fix, config change, spike) is exempt — it uses the lighter Socratic probe in
the Draft-and-React section instead.

### Required input — the six framing fields

1. **Problem** — the observable thing that is wrong
2. **Why now** — the forcing reason this is worth doing now
3. **Decision authority** — who owns the *what* and *why*; `none — [reason]` is a
   valid answer (solo or personal work). `TBD` or blank is not.
4. **Behavioral population** — who must change behavior for this to work;
   `none — [reason]` is valid (a library, a standalone script). `TBD` or blank is not.
5. **Riskiest assumption + liveness** — "betting [X]; wrong when [Y]; would know within ~2 weeks via [Z]"
6. **Success criteria** — the observable real-world outcome

### Two acceptable sources

**Source A — `problem-framing.md` (preferred).** Brainstorming's convergent
interview writes `openspec/changes/{name}/problem-framing.md`. On invocation,
check for it. If it exists with all six fields present and non-TBD, use it as the
confirmed input and proceed to drafting.

**Source B — inline framing.** If there is no `problem-framing.md`, the user may
supply the framing inline. Do NOT silently infer it. Run a SHORT confirmation
pass — one field at a time, each carrying your recommended answer ("Decision
authority: I'd assume this is you — correct?"). The user confirms or corrects
each. This is the grill-me pattern compressed: review-don't-author. When all six
are confirmed, write them to `problem-framing.md` yourself, then proceed.

### Hard stops

- **No framing, OR any of the six fields unfilled (missing, blank, or `TBD`)** →
  do not draft. A field that genuinely does not apply is filled with
  `none — [reason]`, not left blank — so an unfilled field means the question was
  skipped, not answered. There is no "ask and proceed" path: complete the framing
  or do not draft. Route back, naming the specific gap so the user is not sent to
  a blank slate: "I can't design against an unconfirmed framing — [field(s)]
  [is/are] unfilled. Run `/brainstorming` to complete the framing, or give me
  [that field] and I'll confirm it with you." If invoked from a molecule and
  `problem-framing.md` is simply absent, say so directly — do not just say "run
  /brainstorming" in a way that loops.
- **Behavioral population and Problem describe different groups** → flag before
  drafting (this is a flag, not a gate — but raise it): "The problem is framed
  around [group A], but the people who must change behavior are [group B]. Which
  is the real subject?" No discovery rigor fixes a misidentified subject.

### The hook is a floor, not a ceiling

A PreToolUse hook (`discovery_input_gate.py`) mechanically blocks solution
drafting when `problem-framing.md` is missing or has unfilled fields. But the
hook can only check that fields are *filled* — it cannot judge whether the
content is *good*. A riskiest assumption that reads only "we will succeed" passes
the hook. Content quality is the interview's job (brainstorming's forcing check)
and the human's job — never treat a passing hook as a confirmed-quality framing.

### Echo-back before drafting

Once the framing is confirmed, before writing any artifact, echo back the
constraints you are treating as binding: "Designing against: [problem], owned by
[authority], betting [riskiest assumption]. The skeleton will test that
assumption. Proceeding." This makes a bad framing visible at the handoff, not at
audit.

## Interaction Model: Draft-and-React

**Precondition: the Input Gate has passed.** Draft-and-react operates on a
*confirmed* framing. If you have not cleared the Input Gate, you are not in
draft-and-react yet — you are in the gate.

**NOT interrogation.** Be resourceful before asking (SOUL.md principle). Instead:

1. **Acknowledge the read phase.** Before doing any file reads, tell the user:
   "Reading project context..." — this prevents a long silence while scanning files.
2. **Read project context BEFORE responding.** Read CLAUDE.md, existing design docs
   (especially anything in `docs/plans/` and `openspec/changes/`), and any referenced
   PRD. Read 2-3 existing design docs to match the project's voice and conventions.
3. **Draft sections you CAN infer.** Problem statement from context. Non-goals from
   similar docs. Walking skeleton from the domain.
4. **Socratic probe on problem statement (feature/epic only, skip for rapid):**
   If the problem statement was inferred from context rather than stated by the user,
   briefly validate it before drafting further: "Before I draft the design — you said
   the problem is [one-sentence summary]. Does that chain causally? Who's affected if
   we don't solve this?" This is one exchange, not an interrogation. If the user
   confirms, proceed. If they correct, update the problem statement and re-draft.
5. **Surface the ONE question that unblocks the rest.** This must be a **separate
   conversational message** after presenting the draft — not inline in a document or
   file. Surface the question as spoken dialogue, not as text written into an artifact.
   Stop output, ask the question, wait for the answer. Example: "I can draft everything
   except the riskiest assumption — that has to come from you. What are you most unsure
   about?"
6. **Zero questions upfront, one max mid-draft.**

## Modes

**Auto-detection logic (always scan both locations):**
1. If invoked with `edit {change-name}` → **Edit mode** on that change
2. **Always check for existing work** on the topic before creating:
   - Scan `openspec/changes/` for a matching change name
   - Scan `docs/plans/` for a matching design doc
   - If found in `openspec/changes/` → offer Edit mode with a brief characterization:
     "Found existing design for '{name}' — [proposal, design] are written, [specs, tasks]
     remaining. Want to continue it, or start fresh?"
   - If found in `docs/plans/` only → offer to migrate: "Found a legacy design doc for
     this topic. Want to migrate it to the new format, or edit it in place?"
   - If found in BOTH locations → warn about the duplicate, characterize both, ask
     which to use
3. Otherwise → **Create mode**
- Only ask when ambiguous. If the intent is clear, proceed without asking.

### Create Mode

Start from scratch. Read context, create an OpenSpec change, produce artifact drafts,
surface one question.

**Steps (internal — never show these to the user):**

1. Derive a kebab-case change name from the topic
   (e.g., "dark mode support" → `dark-mode-support`)

2. Create the change:
   ```bash
   openspec new change "{name}" --schema {schema}
   ```
   If `openspec/changes/` doesn't exist, the CLI creates it. If it fails for
   any reason, create the directory structure manually and proceed.

3. Get the artifact build order:
   ```bash
   openspec status --change "{name}" --json
   ```
   Parse to get the `artifacts` list with their `status`, dependency order,
   and `applyRequires` (which artifacts must be done before implementation).

4. For each artifact in dependency order:
   a. Get enriched instructions:
      ```bash
      openspec instructions {artifact-id} --change "{name}" --json
      ```
   b. The JSON includes `context`, `rules`, `template`, `instruction`, `outputPath`,
      and `dependencies`. **`context` and `rules` are constraints for YOU — they
      guide what you write but must NEVER appear in the output files.** Do not copy
      `<context>`, `<rules>`, or `<project_context>` blocks into artifacts.
   c. Read any completed dependency artifacts for context.
   d. Write the artifact file using `template` as the structure, applying `instruction`
      guidance and the adversarial thinking overlays from this skill.
   e. After writing each artifact, re-check status:
      ```bash
      openspec status --change "{name}" --json
      ```

5. Continue until all artifacts in `applyRequires` show `status: "done"`.
   The `applyRequires` list comes from the schema's `apply.requires` field.
   Check `openspec status --change "{name}" --json` for the authoritative
   completion gate — do not hardcode artifact lists. As of now:
   `rapid` gates on `[design]`, `feature` on `[tasks]`, `epic` on `[tasks]`.
   All prerequisite artifacts must still be authored in dependency order to
   reach those gates.

**What the user sees:** A draft design document presented in conversation, with the
ONE blocking question surfaced as a separate message after the draft. The user does
NOT see `openspec` commands, artifact IDs, JSON output, or status checks. They see
a thoughtful design emerging.

**Writing:** Once the user approves the draft (explicitly or by resolving the mid-draft
question), write the artifact files immediately. No confirmation needed — draft
approval IS write approval. Announce briefly: "Saving design for '{name}'."

**Done signal:** After all artifacts are written, announce explicitly:
"Discovery complete for '{name}'. Design saved to `openspec/changes/{name}/`."
The path is revealed here so the user can find the files if needed — this is the
ONE place the storage location appears in user-facing output.
If the project uses beads: append "Run `/work-breakdown` to create tasks."
If the project does NOT use beads (no `.beads/` directory): append "Walking skeleton
tasks are in the design doc — add them to your task tracker when ready."

### Edit Mode

Refine an existing OpenSpec change. Read current artifacts, apply changes,
re-validate all sections.

Invocation: `/discovery edit {change-name}`

**Steps (internal):**
1. Read the existing change's artifacts from `openspec/changes/{change-name}/`
2. **Characterize the current state** before presenting options: "This change has
   [list of written artifacts]. [Summary of key decisions: riskiest assumption,
   skeleton scope, N non-goals]. What needs to change?"
3. Draft the changes in conversation
4. Apply changes to the artifact files after user approval

### Unstructured Fallback Mode

When the `openspec` binary is not available, produce the design as a single file at
`docs/plans/YYYY-MM-DD-{topic}-design.md` containing all required sections inline.
Apply the same adversarial overlays. No OpenSpec commands are used.

This also applies when editing legacy design docs found in `docs/plans/` that the user
chooses to edit in place rather than migrate to OpenSpec.

## Scaling

**Schema selection IS the scaling mechanism.** The three schemas encode different
ceremony levels:

| Schema | Time-box | Artifacts | Depth | When | At limit |
|--------|----------|-----------|-------|------|----------|
| `rapid` | 15-30 min | design only | 1-2 sentences each section | Bug fix, small feature, config change, spike | If >30 min, upgrade to feature |
| `feature` | 30-60 min | proposal + design + specs + tasks | Full paragraphs | Cross-cutting feature, new integration | At 60 min: "Spike or over-planning?" |
| `epic` | 60-90 min hard cap | proposal + design + specs + decisions + tasks | Full detail + ADRs | Greenfield, architecture change, new system | At 90 min: hard stop, ship or declare spike |

**Auto-detect when `--schema` is not provided.** Analyze the topic and project context
to select the schema automatically:
- Bug fix, config change, spike, single-file change → `rapid`
- New feature, integration, cross-cutting concern → `feature`
- Multi-system, multi-sprint, architecture change → `epic`

Announce the selection with rationale: "This looks like a quick fix — using rapid
depth. Say 'expand' if you want the full feature flow." The user can always override
with `--schema` or by saying "expand" / "shrink" mid-discovery. If the detected scale
is wrong, the user just says so and Claude re-calibrates immediately.

If `--schema` IS provided explicitly, use it without auto-detection.

**Tie-breaker: when in doubt, choose larger.** If the scale is ambiguous between two
tiers, pick the larger one. Cutting a feature down to rapid mid-stream wastes more time
than starting at feature and finishing early. Specifically: if uncertain between rapid
and feature, choose feature — the cost of one extra artifact is 15 minutes, the cost of
missing a proposal on a cross-cutting change is a false skeleton.

**Announce the scale:** Open with "This looks like a [rapid/feature/epic] change —
adjusting depth accordingly. Time budget: [15-30 / 30-60 / 60-90] minutes."

**Self-calibrating:** Discovery cannot exceed the estimated size of the first skeleton.
If the skeleton is 30 minutes of work, discovery should not take 90 minutes.

## Required Sections — Adversarial Thinking Overlays

The OpenSpec schemas define the artifact structure and section requirements. This skill
adds the adversarial thinking layer on top. When generating artifacts, apply these
overlays to force genuine thinking beyond what the schema templates ask for.

**These overlays are YOUR internal prompts.** They guide how you think about each
section. The adversarial questions (e.g., "What changes in the world?") must NEVER
appear in the output documents. They produce better content — they are not content
themselves. Write the answers, not the questions.

These overlays apply regardless of which schema is used. The schema controls WHICH
artifacts are produced; these overlays control HOW you think about each section.

### Problem Statement (in proposal or design, depending on schema)

> **"What changes in the world?"**

Not "what are we building" — what observable change happens for users, systems, or
the business? If you can't name the change, you don't have a problem yet.

### Non-Goals (in proposal, feature/epic only)

> **"Name three things you are refusing to build."**

If you cannot name three, you have not made real choices. Strategy is what you refuse,
not what you include.

Non-goals are not "things we'll do later." They are things that someone reasonable
would expect to be in scope, and you are explicitly excluding. Each non-goal must:
- Name WHY (capacity? out of scope? future increment?)
- Answer: "What does this choice lock in?" — every non-goal is also an irreversible commitment
- Make at least one person uncomfortable

### Strategic Alternatives (in design, feature/epic only — skip for rapid)

> **"What are the structurally different paths — including not doing this?"**

The "Embedded alternative" in Riskiest Assumption names a different way to *build
the same thing*. This is different: name the alternatives to building it at all.

List 2-3 strategic alternatives, each with a one-line reason it was rejected:
- **Do nothing / defer** — what happens if this is simply not done, or done later?
- **Solve the problem differently** — a structurally different approach, not a
  different implementation of the same one
- **Buy / outsource / adopt** — is there an existing thing that removes the need
  to build?

Each rejection reason must be specific. "We need this" is not a reason — it is the
absence of one. If you cannot articulate why the chosen path beats doing nothing,
you have not earned the work.

If brainstorming ran first, this section captures and confirms the alternatives
its adversarial probe already surfaced — record them, do not re-derive them. If
discovery was invoked directly, generate the alternatives and confirm them with
the user before recording.

This is a **recorded** section in the design document — unlike the pre-mortem and
red/blue team, whose output stays internal. The reader of the design should be
able to see what was on the table and why it was set aside.

### Riskiest Assumption (in design, all schemas)

> **"I am betting [X]. I will know I'm wrong when [Y]."**

Fill in both blanks. The assumption must be:
- Falsifiable (you can describe what "wrong" looks like)
- Central (if it's false, the design changes significantly)
- Testable (you can describe how to test it)

**Embedded alternative (feature/epic only):** Name the specific alternative approach
you rejected, and explain why. The alternative must be specific enough to actually
build. Validation test: could the user read this and immediately implement the
alternative if they wanted to? If not, it's not specific enough.
At rapid scale: one sentence naming the alternative is sufficient.

**Per-assumption probes (feature/epic only — skip for rapid):**
1. What changes about the design if this assumption is false?
2. How quickly would we discover it's false once we start building?
3. Can we test it before writing code? If yes and less than 30 min, test it NOW.

### Pre-Mortem (feature/epic only — skip for rapid)

Before testing the single riskiest assumption, zoom out to the full design:

> **"It's 6 months post-launch. This feature is in crisis. Why?"**

Generate 3-5 failure modes that are NOT the riskiest assumption. These are structural
risks — integration failures, adoption problems, hidden dependencies, operational
gaps. For each failure mode, name a one-sentence mitigation.

If the pre-mortem surfaces a failure mode that is MORE likely than the riskiest
assumption, **revise the riskiest assumption** before proceeding. The pre-mortem
outranks the original assumption if it reveals a bigger risk.

This is a 5-minute exercise. Do not over-produce — 3-5 bullet points, not a report.
Pre-mortem output is internal reasoning. It informs the design but does NOT appear
in the output documents as a separate section.

### Liveness Test (gates skeleton design — run immediately after Riskiest Assumption)

> **"Given your riskiest assumption, what happens if it's false and you don't discover it for two weeks?"**

If the answer is "nothing much" — the assumption isn't actually risky. **Do not
proceed to the Walking Skeleton.** Return to the Riskiest Assumption, find the real
risk, and re-run this test.

If the answer is "significant rework" — good. The skeleton should test this first.
Proceed to Walking Skeleton design.

This is a **hard gate**: no skeleton is designed until the riskiest assumption passes
the liveness test. The skeleton exists to test the assumption — designing a skeleton
around a non-risky assumption is waste.

### Red Team / Blue Team (feature/epic only — skip for rapid)

After all design sections are drafted but BEFORE designing the Walking Skeleton:

> **Red team (2 min):** "Here's why this design fails — name the 3 strongest
> arguments against shipping this as designed."
>
> **Blue team (2 min):** "Here's why we built it this way — defend each design
> choice against the red team's arguments."

If red team surfaces a real issue that blue team cannot defend, revise the design
section before proceeding to the skeleton. If all arguments are defensible, proceed.

This is internal reasoning — red/blue team output does NOT appear in the design
document. It informs the design choices and may strengthen the Decisions or Risks
sections.

### Walking Skeleton (in design, all schemas)

The minimum system that can fail in the most informative way. NOT an MVP, NOT a prototype.

- **What it is:** one sentence
- **What it tests:** the riskiest assumption
- **What done looks like:** observable outcome — not "tests pass," not "code compiles."
  Specific output or behavior in the real world.
- **Task count:** 1-3 tasks at 30-60 min each. If it exceeds this, it's not a skeleton
  — cut until it fits. (rapid: exactly 1 task.)

**Cutting test:** Remove everything except what is required to test the riskiest
assumption. What remains is the skeleton.

Each task is concrete enough to execute without further design. If a task requires
a sub-decision, it's too big — split it.

**Global invalidation rule:** If the skeleton invalidates the riskiest assumption,
return to the Riskiest Assumption section, revise, and re-cut the skeleton. Do not
proceed to future increments on a false foundation. This applies at any schema tier.

### Proof of Delivery (in design, feature/epic; "Done When" in rapid)

> **"I will know this is worth continuing when [X] after I build [Y]."**

Not "when the tests pass." Not "when it deploys." When the actual real-world outcome
is observable. This is the continuation gate for the walking skeleton.

### Anti-Metrics (in design, feature/epic only)

> **"Even if this works perfectly, it has failed if..."**

Name at least one thing that, if it happens, means the solution is wrong even though
it technically works. Examples: "latency exceeds 2s," "requires manual intervention
more than once per week," "users bypass it and use the old workflow."

Minimum 2 for feature, minimum 3 for epic.

### Future Increments (in design, feature/epic; Phased Delivery in epic)

`[PLACEHOLDER]` — NOT designed yet.

List what comes after the skeleton, but do not design it. Each increment gets an
outcome statement:

> "This increment is done when [observable real-world state], not when [common false proxy]."

Future increments are addressed after learning from the skeleton. Designing them now
is waste.

### Open Questions (in design, feature/epic)

Open Questions are unknowns the design surfaced but did not resolve. Each one must
be marked for whether it blocks the walking skeleton:

> **[SKELETON-BLOCKING]** — the skeleton cannot execute, or cannot produce a
> trustworthy signal, until this is resolved.
> **[DEFERRABLE]** — the skeleton can run without it; resolve before the
> increment that actually needs it.

A `[SKELETON-BLOCKING]` open question is not really an open question — it is an
unfinished prerequisite. **Discovery is NOT complete while one remains
unresolved.** Resolve it now (ask the user, read the code) or the skeleton is
designed around a gap. The vue3 discovery deferred two skeleton-blocking unknowns
past completion; the skeleton's hardest step was then designed blind.

## Section Status Tags

Mark each section with exactly one tag:

- `[VALIDATED]` — assumption tested, learning incorporated
- `[PENDING SKELETON]` — designed but not yet tested
- `[PLACEHOLDER]` — not yet designed, addressed after learning

## Spikes

If discovery reveals you need a spike, **that IS the output.** A spike is:
- 1-2 tasks, time-boxed
- A specific question it answers
- A decision it enables

Use `--schema rapid` for spikes. The rapid schema produces a single lightweight
design artifact — perfect for spike scoping.

Do not continue designing past the spike. The spike result feeds back into discovery.

## Output Conventions

### Canonical output location

All new discovery output goes to `openspec/changes/{name}/`. There is no alternative.

```
openspec/changes/{name}/proposal.md     # feature, epic
openspec/changes/{name}/design.md       # all schemas
openspec/changes/{name}/specs/*.md      # feature, epic
openspec/changes/{name}/decisions/*.md  # epic only
openspec/changes/{name}/tasks.md        # feature, epic
```

The only exception is **Unstructured Fallback Mode** (openspec binary not available)
which writes to `docs/plans/YYYY-MM-DD-{topic}-design.md`.

Read existing files in `openspec/changes/` and `docs/plans/` to match the project's
voice and conventions, but new work always goes to `openspec/changes/`.

### Completion criteria

Discovery is COMPLETE when:
- All artifacts in `applyRequires` show `status: "done"` (check via
  `openspec status --change "{name}" --json`)
- Riskiest assumption passed the liveness test and has a test plan
- Strategic Alternatives section exists (feature/epic) — at least 2, each with a
  specific rejection reason
- Walking skeleton tasks exist (in tasks.md for feature/epic, inline in design.md for rapid)
- Walking skeleton has at most 3 tasks (rapid: exactly 1) — an enforced gate, not
  an aspiration. If the skeleton exceeds it, re-cut before completing.
- No `[SKELETON-BLOCKING]` open question remains unresolved
- User has approved the draft
- Done signal has been announced to the user

### Integration with beads

Walking skeleton tasks feed `/work-breakdown` which creates bead tasks. Each task
references the change name. Discovery feeds beads; beads do not feed discovery.

If the project does not use beads (no `.beads/` directory), skeleton tasks are
documented in the design but not automatically imported into any task tracker. The
done signal notes this for the user.

### Integration with mol-feature

When invoked from a mol-feature molecule (discovery step), the change name should
match the molecule's `name` variable. The molecule's discovery step expects output
at `openspec/changes/{name}/`.

**When discovery completes, close the molecule step:** Run `bd close <step-id>` where
`<step-id>` is the discovery step's bead ID. This triggers the review-discovery gate.
Do not show the `bd close` command to the user — just announce "Discovery complete"
and the review gate will activate.

## What the User Sees vs. What Happens

| User sees | What actually happens |
|-----------|----------------------|
| "Reading project context..." | CLAUDE.md, design docs, PRDs being read |
| "This looks like a feature change..." | Schema auto-detected or explicit, `openspec new change` runs. If user corrects ("no, this is rapid"), switch immediately and re-announce. Schema selection is never final until the user approves the draft. |
| Draft design appearing in conversation | Artifacts being composed from schema instructions |
| [separate conversational message with one question] | Mid-draft question — a conversational message, never inline in a file |
| "Saving design for '{name}'." | Artifact files written to OpenSpec change folder |
| "Discovery complete for '{name}'. Design saved to openspec/changes/{name}/." | All `applyRequires` artifacts have `status: done`; path revealed once for findability |

**Never show to the user (except where noted):**
- `openspec` CLI commands or their output
- Artifact IDs (proposal, design, specs, tasks, decisions)
- `.openspec.yaml` files or their contents
- JSON output from `openspec status` or `openspec instructions`
- Storage paths (`openspec/changes/...`) — EXCEPT in the done signal, where the path
  is revealed once so the user can find the files
- The word "artifact" in the OpenSpec sense — use "section" or "document" instead
- Adversarial prompt questions (e.g., "What changes in the world?") — write the
  answers, not the questions
- `bd close` or other beads commands used for molecule integration
- `bd audit record` or any audit/telemetry commands

## Completion Logging (silent — never surface to the user)

After the done signal is announced and all artifacts are written, silently record
a completion event. This runs after every successful discovery completion — the
user must never see the command or its output.

```bash
echo '{"kind":"discovery_complete","extra":{"target":"<change-name>","schema":"<rapid|feature|epic>","scale_auto_detected":<true|false>,"scale_overridden":<true|false>,"liveness_test_passed_first_try":<true|false>,"pre_mortem_changed_assumption":<true|false>,"sections_generated":<N>,"mode":"<create|edit|legacy_edit>"}}' | bd audit record --quiet
```

Field definitions:
- `target`: the kebab-case change name
- `schema`: which schema tier was used (rapid, feature, or epic)
- `scale_auto_detected`: true if the schema was auto-detected, false if `--schema` was explicit
- `scale_overridden`: true if the user changed the schema mid-discovery (e.g., said "expand" or "shrink")
- `liveness_test_passed_first_try`: true if the riskiest assumption passed the liveness test without revision
- `pre_mortem_changed_assumption`: true if the pre-mortem caused the riskiest assumption to be revised (feature/epic only; always false for rapid)
- `sections_generated`: count of artifact files written (e.g., 1 for rapid, 4-5 for feature)
- `mode`: "create" for new changes, "edit" for existing changes, "legacy_edit" for docs/plans/ edits

If the `bd audit record` command fails (e.g., no `.beads/` directory), ignore the error silently. Logging must never block the user flow.
