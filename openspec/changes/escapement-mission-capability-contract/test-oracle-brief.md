# Test Oracle Brief — Escapement mission and capability contract

## 1. Business invariant

Escapement must be presented and instructed consistently as a client-neutral system that converts available agent capacity plus delegated authority into verified, delivered outcomes while reserving human attention for consequential choices.

The contract must preserve design/specification, dependency-aware work breakdown, independent verification, and authorized landing as first-class capabilities. Routine means already delegated by a bounded outcome must not be reframed as new product decisions. Client-specific sections may name tools, but must state only fixture- or point-of-effect-proven behavior.

## 2. Independent source of truth

The approved OpenSpec requirements define the expected semantics independently of the renderer and generated files:

- The canonical mission is: “Escapement converts available agent capacity plus delegated authority into verified, delivered outcomes while reserving human attention for consequential choices.”
- The capability chain is, in order: intent and authority; design and specification; executable dependency-aware work breakdown; capacity allocation; isolated execution; action-local continuation and repair; independent outcome verification; authorized landing and delivery; learning and feedback.
- Ordinary means are: established worktree; scoped inspect/edit; tests/lint/build; commit; task-branch push; pull-request create/update; causal CI/review repair; repository-declared landing and verification.
- Consequential choices are: changed intent/non-goals; material outcome trade-off; undelegated repository/account/audience; new privilege/credential; destructive or irreversible shared effect; actually enforced confirmation class; unsafe owner overlap; missing standard landing path.
- Effective adapter support comes from manifest status plus named fixtures and point-of-effect code, not prose. The current merge hook resolves repository authorization but does not prove green status or enforce `confirm_class`; deploy metadata is informational; and the installed Codex adapter does not mechanically intercept final responses.
- Filesystem and manifest inventories, not prose, determine mutable counts.

`agent-surfaces/identity.json` will become the canonical distribution input, but it is not allowed to prove its own semantic correctness; the approved OpenSpec contract is the oracle for the identity data.

The test suite, not the renderer, owns these inventories:

- **Mission-bearing authored surfaces:** `agent-surfaces/onboarding/shared.md`, `README.md`, `docs/VOCABULARY.md`, `docs/NAMING.md`, and `docs/deck.html`.
- **Mission-bearing generated surfaces:** `AGENTS.md`, `CLAUDE.md`, `plugins/escapement/.codex-plugin/plugin.json`, `plugins/escapement-claude/.claude-plugin/plugin.json`, and `.claude-plugin/marketplace.json`.
- **Full capability-chain surfaces:** `agent-surfaces/identity.json`, `agent-surfaces/onboarding/shared.md`, `README.md`, and `docs/deck.html`.
- **Authority/doctrine surfaces:** `agent-surfaces/onboarding/authority.md`, generated `AGENTS.md` and `CLAUDE.md`, `agent-surfaces/openspec/apply.md`, `agent-surfaces/openspec/explore.md`, `claude/skills/beads-execution/SKILL.md`, `claude/rules/outcome-ownership.md`, `claude/rules/continuation-harness.md`, `claude/rules/agent-teams-default.md`, `claude/rules/molecule-awareness.md`, `harness/bin/winddown_judge.py`, and `harness/bin/winddown_gate.py`.
- **Authoritative support-region surfaces:** `agent-surfaces/onboarding/authority.md`, `README.md`, `docs/VOCABULARY.md`, `docs/deck.html`, generated `AGENTS.md` and `CLAUDE.md`, `claude/rules/outcome-ownership.md`, and `claude/rules/continuation-harness.md`. Each carries exactly one manifest-bound region. `agent-surfaces/onboarding/hosts/codex.md` is an additional support-narrative surface whose prose is validated against the same evidence matrix without duplicating the structured authority region when composed into `AGENTS.md`. Tests own the applicable surface subset for each claim and inject a false claim into each independently.

The test suite also owns this current support-claim matrix, derived from executable point-of-effect evidence rather than identity data:

| Claim | Current truthful status | Executable control |
|---|---|---|
| Merge authorization observes green status | unsupported | An authorized merge payload with no status/check evidence is allowed by `merge_authorization_gate` |
| `confirm_class` blocks a matching merge | reserved / unenforced | An authorized declaration with a non-empty `confirm_class` is still allowed by the current merge gate |
| Deploy metadata executes or authorizes a deployment command | informational only | Resolver outcomes are identical with and without deploy metadata, and no deployment action is invoked |
| Codex mechanically intercepts final responses | unsupported / guidance-only | Codex generated hooks contain no Stop/final-response event while the SessionStart advisory names the gap |

## 3. Solution constraints

- Edit authored owners, never generated `AGENTS.md`, `CLAUDE.md`, or plugin copies directly.
- The renderer must consume `agent-surfaces/identity.json`; `manifest.json` remains adapter capability/evidence authority.
- Public presentation files remain authored, with executable consistency validation.
- Core identity regions must be tool/client neutral; explicit adapter/integration regions must remain tool-specific.
- Surface inventories used by mutation tests must be fixed test constants and must not be read from renderer target enumeration.
- Support-status validation must be driven by a structured claim matrix and the corresponding executable controls; phrase bans alone are insufficient.
- Every authoritative support-region surface must carry exactly one structured status region whose IDs, statuses, and reasons match the fixed point-of-effect evidence matrix. Additional host narrative must not duplicate that authority. Semantic negative controls must include paraphrases before, inside, and after the region, not only one forbidden sentence.
- The identity source must propagate both mission and capability data into generated instructions; a capability sentinel must fail a renderer that copies the authored capability prose.
- Every core identity field is tool-neutral, while the current adapter mapping is an exact capability-to-tool contract rather than a bag of names.
- Rendering must prepare and validate targets before replacing either published plugin tree.
- Publishing must be transactional across individual generated files and both plugin trees; an injected replacement failure must restore every prior surface.
- No runtime authority broker, scheduler, prompt suppressor, Pi adapter, OPA, Cedar, or other policy engine enters this change.
- Unsupported behavior must remain explicit.
- Mutable inventory counts must be derived or removed.
- Tests belong in a focused sibling such as `tests/test_mission_capability_contract.py`, not the already large agent-surface suite.

## 4. Invalid solution classes

- README-only rewrite.
- Copying the mission everywhere while the renderer ignores the canonical identity source.
- Defining the core through OpenSpec, Beads, GitHub, Claude Code, Codex, Pi, OPA, or Cedar.
- Implementing neutrality by banning tool names from legitimate adapter sections.
- Leaving live-enforcement claims for green status, `confirm_class`, deployment, or Codex final-response interception.
- Accepting a paraphrased live-enforcement claim because it differs from a forbidden sentence.
- Letting the manifest certify a false reason, duplicate a claim ID, or publish duplicate structured authority.
- Mapping a current tool to the wrong capability while preserving every expected tool name.
- Deleting a valid generated tree before discovering that new identity input is invalid.
- Partially publishing a new generation when one replacement fails.
- Letting an invoked skill turn one ambiguous task or discrepancy into a session-global pause while independent ready work exists.
- Updating mutable counts to today's values.
- Omitting design or executable work breakdown from the capability chain.
- Treating every discovered issue as delegated or every unresolved question as session-global.
- Hand-editing generated targets.

## 5. Fragile implementation to reject

The primary tempting shortcut is to update the README hero and tagline while leaving onboarding, runtime rules, vocabulary, generated instructions, plugin metadata, and the deck contradictory.

The secondary shortcut is to hard-code the approved mission into renderer outputs without reading the canonical identity source. The tests must mutate the identity source to a valid sentinel and prove rendered outputs follow it.

## 6. Negative controls

- Restore the legacy README/OpenSpec-first wording in one identity-bearing surface; validation must fail and name that path.
- Parameterize every test-owned mission surface, corrupt it independently in a copied repository, run the public validator, and require non-zero exit plus the exact divergent path.
- Change only the identity mission to a valid sentinel, render a copied repository, and require the sentinel in every test-owned generated identity target; a hard-coded renderer must fail.
- Change one identity capability to a valid sentinel for rendering and require it in generated instructions.
- Inject each forbidden current tool/client name into the core mission; validation must fail.
- For each test-owned full capability-chain surface, delete or reorder one capability—especially design/specification or work breakdown—and require failure naming that surface.
- Replace action-local continuation with “pause until answered”; the doctrine assertion must fail.
- Execute the support-claim controls: allow an authorized merge with no green evidence; allow an authorized merge despite non-empty `confirm_class`; compare resolver behavior with/without deploy metadata and use a deploy-command sentinel file to prove no invocation; prove Codex has no Stop event and its advisory fires. For every applicable support-claim surface, inject each false claim independently and require validator failure naming that path.
- Inject materially equivalent paraphrases of every false support claim before, inside, and after the authoritative region and require the validator to reject them.
- Change a manifest reason to claim unsupported enforcement, duplicate a support claim ID, and duplicate a structured region; each mutation must fail before publication.
- Move the canonical mission outside a core-identity region while leaving it elsewhere in the file; validation must reject the surface.
- Put tool ownership into `operating_model` or `principles`, and assign GitHub to design/specification; validation must reject the identity path.
- Render with an empty but syntactically valid identity mission and prove existing generated trees remain byte-preserved.
- Inject a publication failure after at least one replacement and prove every prior generated file and plugin tree is restored.
- Restore global `STOP and ask`, `Implementation Paused`, or permission-offer language in an invoked execution skill; doctrine validation must fail.
- For every authored public surface, plant digit, word-number, and hyphenated mutable inventory phrases—at minimum `eight skills`, `46 hooks`, `9 steps`, and `9-step workflow`—and require failure naming the path. Stable semantic counts such as the nine durable capabilities remain allowed.
- Remove current adapter mappings; the positive integration assertion must fail.

## 7. Positive controls

- An explicit adapter section naming OpenSpec, Beads, worktrees, Claude Code, Codex, and GitHub passes.
- The exact nine-capability chain remains present, including design and work breakdown.
- Codex may truthfully say supported lifecycle and PreToolUse behavior is ready while final-response interception is unsupported.
- Repository-declared ordinary means and consequential boundary conditions remain present.
- Generated Codex and Claude instructions plus plugin metadata contain the canonical identity after rendering.
- Public narrative may vary stylistically outside designated identity-bearing regions.
- Every support-claim evidence selector executes successfully as part of the targeted suite; fixture existence alone is not accepted.

## 8. Missing/unresolved handling

Fail closed for missing or malformed identity data, missing required capability labels, missing identity-bearing regions, unknown adapter status, unsupported claims without evidence, missing fixture selectors, and mutable counts without an authoritative derivation.

Explicit `unsupported` or `guidance-only` status is valid. Unknown clients need not be claimed. Presentation text outside identity-bearing regions may vary and should not fail merely for mentioning tools.

## 9. Final outcome verification

Run:

```bash
openspec validate escapement-mission-capability-contract --strict
python3 -m pytest tests/test_mission_capability_contract.py -q
python3 -m pytest tests/test_agent_surfaces.py -q
python3 tools/render_agent_surfaces.py --check
```

Inspect rendered `AGENTS.md`, `CLAUDE.md`, both plugin manifests/marketplaces, README, vocabulary, naming, and deck identity sections. After merge and deployment, verify the effective installed Codex and Claude plugin sources expose the new mission/doctrine and retain truthful adapter limitations. Do not accept native prompt suppression or action-local scheduler enforcement as delivered by this change.

## Proposed checks and mutant coverage

| Proposed check | Mutants it must kill |
|---|---|
| `test_identity_contract_matches_approved_semantics` | missing design/work breakdown; changed mission semantics |
| `test_renderer_consumes_canonical_identity_source` | hard-coded renderer; identity source unused |
| `test_renderer_consumes_canonical_capability_source` | copied capability prose; identity capabilities unused |
| `test_all_identity_bearing_surfaces_agree` | README-only or plugin-only rewrite using a fixed test-owned surface inventory |
| `test_each_identity_surface_mutation_fails_and_names_path` | validator silently omits a public, runtime, or generated surface |
| `test_core_identity_rejects_tool_and_client_names` | OpenSpec/Beads/client-defined core |
| `test_adapter_sections_preserve_current_tool_mappings` | over-broad tool-name ban |
| `test_each_capability_surface_contains_ordered_chain` | capability laundering into identity JSON only; missing/reordered design or work breakdown |
| `test_delegated_authority_includes_all_ordinary_means_and_boundaries` | incomplete routine authority; “fix everything” scope expansion |
| `test_action_local_continuation_preserves_independent_work` | global pause doctrine; side-question replacement |
| `test_support_claims_match_executed_point_of_effect_controls` | false green/confirm/deploy/final-response claims or unexecuted fixture references |
| `test_paraphrased_false_support_claims_fail_validation` | exact-sentence blacklist that accepts equivalent lies |
| `test_wrong_capability_to_adapter_mapping_fails_validation` | tool-name bag with incorrect capability ownership |
| `test_invalid_identity_does_not_delete_existing_generated_tree` | delete-before-validate renderer failure |
| `test_authored_surfaces_reject_digit_word_and_hyphenated_inventory_counts` | updating stale counts to today's value or changing the phrasing |
| Existing renderer drift checks | direct edits to generated targets |
