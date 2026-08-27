## ADDED Requirements

### Requirement: Primary-checkout lifecycle is packaged identically

The system SHALL package the public worktree command and every runtime module
needed for primary-checkout synchronization in both Claude and Codex plugin
surfaces from the same committed source.

#### Scenario: Generated packages are checked

- **WHEN** `python3 tools/render_agent_surfaces.py --check` and package parity tests run
- **THEN** both plugin packages contain byte-identical primary-checkout lifecycle code and a runnable `sync-root` command

#### Scenario: Runtime module is omitted from one host

- **WHEN** a generated Claude or Codex package lacks a module required by `sync-root`
- **THEN** the generated-surface or package-parity check fails

### Requirement: Root protection claims match supported host behavior

The generated surfaces SHALL register root-checkout mutation protection only for
host events whose payload provides enough path context for the guard to classify
the target safely, and SHALL document unsupported boundaries.

#### Scenario: Claude explicit-path edit event

- **WHEN** the Claude plugin handles a supported explicit-path edit event
- **THEN** the packaged root-checkout guard is registered and can reject a primary-checkout target

#### Scenario: Host omits effective command working directory

- **WHEN** a host hook payload cannot identify the effective mutation target
- **THEN** the surface does not claim or register a blocking root guard for that event
