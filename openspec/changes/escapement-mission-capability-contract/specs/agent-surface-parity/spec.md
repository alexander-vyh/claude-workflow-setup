## MODIFIED Requirements

### Requirement: Neutral manifest renders host instruction surfaces

The system SHALL render committed Claude and Codex instruction surfaces from a host-neutral capability manifest, a canonical structured identity source, and shared onboarding fragments. The system SHALL validate authored public identity surfaces against the same canonical mission without requiring adapter-specific sections to be host-neutral.

#### Scenario: Generated instruction files are current

- **WHEN** `python3 tools/render_agent_surfaces.py --check` is run from the repo root
- **THEN** the command succeeds only if `AGENTS.md`, `CLAUDE.md`, `.codex/hooks.json`, and generated plugin metadata match the canonical inputs

#### Scenario: Hand-edited generated file drifts

- **WHEN** a generated target differs from the canonical rendered output
- **THEN** the check command fails and names the drifting target

#### Scenario: Public identity diverges

- **WHEN** an authored public identity section omits or contradicts the canonical mission, durable capabilities, or replaceable-adapter boundary
- **THEN** the narrative consistency check fails and names the divergent surface

#### Scenario: Adapter section names a host

- **WHEN** an explicit adapter status or integration section names a supported host or current tool
- **THEN** the narrative consistency check accepts it provided the claim matches manifest and fixture evidence

## ADDED Requirements

### Requirement: Identity checks reject superficial alignment

The surface checks SHALL reject a change that updates only one presentation surface while leaving runtime guidance, generated metadata, or public definitions contradictory.

#### Scenario: README-only mission rewrite

- **WHEN** README contains the canonical mission but onboarding, generated surfaces, plugin metadata, or vocabulary retain a conflicting product definition
- **THEN** the surface checks fail

#### Scenario: Tool-neutral core preserves useful integrations

- **WHEN** core identity is tool-neutral and current integration sections still map OpenSpec, Beads, worktrees, clients, and landing systems to capabilities
- **THEN** the surface checks pass

### Requirement: Mutable inventory claims are derived or removed

Public surfaces SHALL derive formula, skill, hook, and adapter inventory claims from authoritative sources or omit exact counts.

#### Scenario: Hand-maintained count drifts

- **WHEN** a public surface contains an exact mutable inventory count that disagrees with its authoritative source
- **THEN** the narrative consistency check fails
