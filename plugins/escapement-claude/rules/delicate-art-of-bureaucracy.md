# The Delicate Art of Bureaucracy — Global Rule

This repo is a bureaucracy. The hooks, skills, gates, rules, and harnesses
in `escapement/` are a structured set of routines that turn
problem-solving successes into reusable practice. That is not pejorative —
it is the operative frame.

Bureaucracies decay in predictable directions unless designed to stay
**lean, learning, and enabling**. The four design features below distinguish
enabling from coercive bureaucracy (Adler & Borys, 1996). Walk them before
adding or modifying any gate.

For the implementation patterns that make a gate satisfy these features —
escape paths, persistent signal, value-not-presence validation — load the
`gate-design` skill (its 3-rule checklist also stays resident at
`claude/rules/gate-design.md`).

## When this rule applies

When you are about to:

- Add a new hook, gate, rule, or skill to this repo
- Modify the denial-message language of an existing gate
- Decide whether to keep, revise, or retire an existing rule
- Design a waiver / exemption / override path
- Triage a complaint that a gate is producing friction

…walk the four design features explicitly. Name the failure mode you are
preventing. State the signal the gate will produce. Make the rationale
visible in the artifact itself.

When this rule conflicts with another rule, the resolution belongs in the
discussion, not in silent suppression of one or the other. Surface the
tension, name which design feature is being traded for which, and decide
deliberately.

<!-- escapement:detail:start -->

## The four design features (Adler & Borys, 1996)

Every gate, hook, rule, skill, and harness in this repo must be evaluated
against these four properties. They are not slogans; they are testable.

### 1. Repair
Can the practitioner repair the process themselves when it breaks, or does
every deviation force escalation? Enabling: the breakdown becomes signal —
the user sees what went wrong, can fix it locally, and the system learns
from the fix. Coercive: deviations are suspect; the only response is to
call a specialist or wait for approval.

**Test for a new gate:** when this gate fires incorrectly, can the user
unblock themselves with information surfaced in the denial, or do they have
to escalate, paper over, or disable the gate?

### 2. Internal transparency
Does the rule expose its rationale to the user, or is it a flat assertion
of duty? Enabling: the rule explains *why*, surfaces metrics so the user
can self-assess, and the procedure becomes a working tool rather than a
hurdle to circumvent. Coercive: rules are issued for compliance; the
rationale is "the engineer's province" and not the user's business.

**Test for a new gate:** does the denial message include the rationale and
the specific path to compliance? Or just the prohibition?

### 3. Global transparency
Does the practitioner see where this rule fits into the wider system, or
only their slice of it? Enabling: the user understands the broader
workflow the gate is protecting and can reason about edge cases. Coercive:
panoptic — the system sees everything, the user sees only the cell they
are in ("that's not your job").

**Test for a new gate:** is the gate's role in the overall workflow
discoverable from `CLAUDE.md`, `claude/rules/`, or the rule's own header?
Can a new agent figure out *why this exists* without reverse-engineering?

### 4. Flexibility
Are deviations treated as risks to be minimized or as learning opportunities
to be captured? Enabling: multiple legitimate paths are documented with
guidance for choosing; reasoned exceptions are first-class. Coercive: any
deviation requires superior approval, which means in practice it requires
work-arounds.

**Test for a new gate:** is there a documented waiver path that requires a
*reason* (not a checkbox), and does the reason text feed back into
improving the rule, or is the only escape "disable the gate"?

## The four failure modes

Bureaucracies degrade in predictable directions. Each direction has a name.
Recognizing them early is the work.

- **Bloated** — heavy controls, lots of busywork, more rules than the risk
  warrants. Symptom: the user spends more time managing the workflow than
  doing the work.
- **Petrified** — rules that outlived the problem they were created to
  solve. Symptom: a rule has lived for a year without revision and nobody
  remembers why it exists.
- **Coercive** — gates whose purpose is to say "no" rather than enable the
  next step. Symptom: denial messages are punitive, not actionable.
- **Mock bureaucracy** — rules that are followed for symbolic value but
  ignored in practice. Symptom: agents satisfy the gate (fake `--spec-id`,
  throwaway waiver text) without performing the underlying work. Wiesche,
  Schermann & Krcmar (2013) found that *both* enabling AND coercive designs
  can produce mock bureaucracy if implementation conditions are wrong —
  enabling-by-design is not a guaranteed cure.
  [per published abstract; full paper not accessed]


<!-- escapement:detail:end -->
## Operating rules

1. **Every rule has a half-life.** Annual review minimum. A rule unrevised in a year is a
   candidate for re-justification, not veneration.
2. **Every gate produces signal.** Waivers are labeled training data; denials are
   questions the system is asking. A gate producing no usable signal is bloat.
3. **Design intent does not survive implementation.** A gate designed enablingly will be
   experienced coercively if deployed without context, trust, or the practitioner's input
   on its rationale. How it ships matters as much as how it is designed.
4. **Behavior precedes belief.** The user need not believe in a new gate first. Run it,
   observe the friction, revise.
5. **Coercion is a smell, not a strategy.** A gate that exists primarily to block —
   without a corresponding affordance to *unblock* — is on the coercive axis. Add the
   affordance or remove the gate.

The lineage and full rationale (Gouldner → Adler & Borys → Shook → Schwartz) live in the
`gate-design` skill.
