# TDD Enforcement — Global Rule

## Outcome -> Oracle -> Constraints -> Tests -> Code

For any non-trivial implementation task, do not start implementation until the
test strategy has been reviewed for oracle quality.

Required sequence:
1. Clarify the business/user outcome.
2. Identify the independent source of truth.
3. Identify solution constraints imposed by the repo, architecture, language,
   framework, security model, and maintainability requirements.
4. Write or update tests/checks that define the valid solution space:
   - behavioral tests for the desired outcome
   - positive controls so the fix does not make the result empty
   - negative controls for known bad cases
   - contract tests for public interfaces
   - architecture/static checks for invalid implementation classes
5. Challenge the tests with plausible bad implementations.
6. Choose the smallest implementation approach that satisfies the tests and
   constraints.
7. Implement.
8. Verify the final user-facing/business-facing outcome.

Passing tests are not enough if the tests could pass a fragile or invalid
implementation.

## Test Oracle Brief Required

Before writing implementation code for any non-trivial change, the QC/test lane
must produce a Test Oracle Brief.

The brief must include:
1. Business invariant: what user/business outcome must be true?
2. Independent source of truth: what determines correctness independently of
   the implementation?
3. Solution constraints: what repo, architecture, language, framework,
   security, ownership, or maintainability constraints must hold?
4. Invalid solution classes: what kinds of implementations are disallowed even
   if they appear to produce the output?
5. Fragile implementation to reject: name at least one tempting shortcut the
   tests must fail.
6. Negative control: what fixture, row, request, role, input, or scenario
   should fail if the code is wrong?
7. Positive control: what proves valid output is not accidentally dropped?
8. Missing/unresolved handling: should missing lookup/source data fail closed,
   fail open, or be explicitly allowed?
9. Final outcome verification: what command, query, report, API call, UI flow,
   or workflow proves the actual result?

A test plan fails review if the named fragile implementation would pass every
behavioral, fixture, contract, architecture, and static check.

<!-- escapement:detail:start -->

### Rapid form (low-blast-radius changes)

The 9-section brief is the default. A low-blast-radius change may use exactly three
Markdown sections when their labeled evidence is semantically complete:

1. **Business invariant** — `Outcome`, `Independent source of truth`, `Binding
   constraints`, and every rapid eligibility decision. Each protected-surface field
   must be exactly `no`. Root cause must include observed executable evidence as
   `Root cause: <Command|Query|API|Report|UI>: <action>; Expected: <cause>;
   Actual: <same cause>; Match: yes`. The planned proof below is also the executable
   outcome-oracle attestation, so rapid does not duplicate that authority in a
   second yes/no field.
2. **Negative control** — `Named fragile implementation`, `Negative control`,
   `Positive control`, and `Missing/unresolved handling`. When drop-all or empty
   output could pass, the positive control must preserve a valid result and uses the
   same planned-proof syntax as user-facing verification. Its expected result cannot
   be empty, dropped, suppressed, absent, or negated. Rapid always records a concrete
   positive control and missing-data disposition; `N/A` or an inapplicability claim
   requires the full lane rather than prose interpretation by the gate.
3. **Final outcome verification** — `Exact user-facing verification` uses
   `<Command|Query|API|Report|UI>: <action>; Expected: <result>`. Planned proof is
   enough for edits and an early durable task-branch commit or push. PR creation
   additionally requires `Focused proof result: Expected: ...; Actual: ...; Match:
   yes`, `Objective blockers: none`, `Known limitations: none`, and structured
   remaining landing proof. Merge or task closure also requires `Observed result:
   Expected: ...; Actual: ...; Match: yes`. In observed proof, `Actual` must equal
   `Expected`; focused and final observed proof must also equal the planned expected
   result. `Command` actions must resolve an executable and use parseable argv with
   an option, path, or qualified argument; `Report` actions begin with `Run` or
   `Generate` followed by that command shape. This rejects sentence-shaped fictional
   commands. Dynamic `eval` in a finishing shell command is treated as final and
   therefore requires final proof.

Rapid is fail-closed. Every exclusion below must be explicitly absent; `unknown`
means use the full form:

- Authorization/security
- Money or sensitive data
- Production mutation
- Schema/migration
- Public contracts
- Irreversible external effects
- Shared infrastructure
- Root cause is not known
- Executable outcome oracle is absent

Stop rapid execution in the current run and move to the full lane if a protected
surface is discovered, the boundary expands, reversibility becomes uncertain,
discriminating controls cannot be constructed, root cause remains unresolved, or
the outcome oracle is missing.

The fragile-implementation challenge is mandatory in both forms. The rapid form
compresses presentation; it never drops independent truth, binding constraints,
discriminating controls, missing-data behavior, or final user-facing proof.


<!-- escapement:detail:end -->
## Implementation-Echo Tests Are Not Accepted

A test is an implementation echo if it passes by repeating the same constant,
algorithm, private helper, mock interaction, generated ID, or intermediate
implementation detail used by production code.

Reject or rewrite tests that:
- use the same magic constant or generated ID as the implementation
- recompute the same algorithm as the implementation
- assert private helper calls instead of externally visible behavior
- mock the thing being tested and only assert that the mock was called
- validate an intermediate artifact when the user cares about final output
- would pass the shortcut implementation the user explicitly rejected
- have no negative control for the bug or behavior they claim to protect
- cannot explain the business invariant they protect

A test that fails before implementation is still insufficient if it would also
pass the known fragile implementation.

## When TDD Applies

Before writing any implementation code in a repo that has a `tests/` directory
or test files at the project root (e.g., `test-*.js`):

1. Write the Test Oracle Brief
2. Review the brief for oracle quality
3. Write the failing test FIRST
4. Run it — verify it fails for the right reason
5. Challenge the tests against plausible bad implementations
6. Write minimal implementation to pass
7. Run it — verify it passes
8. Refactor if needed, keeping tests green

Never silently skip TDD in a test-capable repo. Either follow it or get explicit
permission to skip.

## Exemptions

- Test files themselves
- **Passive** config and docs (`.toml`, `.yaml`, `.json`, `.md` that an app merely *reads* as data — the app's own tests cover behavior). This does NOT cover *behavioral* config — see "Behavioral config is not exempt" below.
- Files in `scripts/`, `bin/`, `tools/`, `scratch/`, `spike/`
- Outside git repos
- User says "prototype", "spike", "throwaway", "one-off", "experiment"
- Bug fixes and chores in repos without test infrastructure

## Behavioral config is not exempt

The exemption above covers *passive* config — files an application reads as data, where the application's own tests cover behavior. It does NOT cover **behavioral config**: files that *drive runtime behavior* and break silently and expensively in production. These include:

- GitHub Actions / CI workflow YAML
- Terraform / OpenTofu / HCL
- Kubernetes manifests
- Airflow DAGs / pipeline definitions
- dbt configs and similar

For behavioral config, "it parses" and "it's well-formed" are **gates, not oracles**. A schema-valid workflow can still suppress a deploy trigger; a valid HCL plan can still destroy the wrong resource. The verification owed scales with behavioral risk:

<!-- escapement:detail:start -->

| Rung | What it proves | Required |
|------|----------------|----------|
| Parse | valid syntax | gate only — never sufficient alone |
| Schema-lint (`actionlint`, `tofu validate`, `kubeconform`) | well-formed + self-consistent; catches shellcheck / expression-context / type / ref errors | **mandatory on every behavioral-config change** |
| Predict (`tofu plan` + deterministic JSON assertion; `kubectl apply --dry-run=server`) | the change-set this produces vs current state | required for IaC / manifest config-authoring changes |
| Observe (`gh workflow run` + assert the downstream run started; apply-to-sandbox / terratest) | the actual behavior happened | required for trigger / auth / deploy-gating logic — the only oracle for that class |


<!-- escapement:detail:end -->
**Lint alone is forbidden as the verification for trigger / auth / deploy-gating changes.** Canonical counterexample: a GitHub workflow whose `GITHUB_TOKEN`-authored merge silently fails to re-trigger `on: push` (GitHub's server-side recursion guard) is schema-perfect and behaviorally broken — no parse or lint catches it; only observing a real trigger does.

**When a behavior genuinely cannot be reproduced locally** (platform semantics, no sandbox available), that is a **structured waiver, not an exemption**: state (a) why it cannot repro, (b) the platform behavior at risk, (c) the post-merge observation that WILL confirm it (e.g. `gh run list --workflow=deploy.yml` shows a run for the merged SHA), (d) a human ack. A waiver makes the gap visible and time-bound; silence makes it a production incident.

Caveat: the Predict and Observe rungs need state + credentials + connectivity, so "deterministic" means *deterministic given creds*, not offline. Terraform import-block planning in particular does a **live provider read per target** — verified 2026-05-20: `tofu plan` on a config with an `import` block issues a real provider read of the target id (fails with a credential/connectivity error when offline), whereas the identical config without the import block plans cleanly offline. So an `import`-block contract's `verification_command` cannot be assumed runnable in a credential-free context.
