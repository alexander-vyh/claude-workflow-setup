## ADDED Requirements

### Requirement: Outcome delegation includes ordinary means

When a user delegates a bounded build, fix, change, execution, delivery, or shipping outcome, Escapement guidance SHALL treat routine, proportionate actions necessary to achieve and verify that outcome as already delegated within the named repository, systems, and constraints. Ordinary means SHALL include the established worktree, scoped inspection and editing, tests, lint, builds, commits, task-branch pushes, pull-request creation and updates, causal CI or review repair, and the repository-declared landing and verification path.

#### Scenario: Agent reaches a routine delivery step

- **WHEN** an agent reaches an ordinary means already covered by the delegated outcome or repository landing policy
- **THEN** the agent continues without asking the user to reconfirm the product decision

#### Scenario: Host still requires approval

- **WHEN** a client mechanically requires approval for an ordinary means
- **THEN** the request is classified as an adapter enforcement limitation rather than a new intent decision, and independent authorized work continues

### Requirement: Authority follows causal scope

A discovered issue SHALL be treated as in scope only when it causally blocks the delegated outcome and its repair does not materially expand requested behavior, repository set, audience, privileges, destructive effects, or another owner's work.

#### Scenario: Bounded blocker is discovered

- **WHEN** a defect directly prevents the delegated outcome and can be repaired within the existing authority boundary
- **THEN** the agent owns and repairs it without asking whether to continue

#### Scenario: Adjacent issue is discovered

- **WHEN** an issue is useful but does not causally block the delegated outcome
- **THEN** the agent records it separately and continues the delegated work without executing the adjacent scope

### Requirement: Human attention is reserved for consequential choices

Escapement guidance SHALL request human attention only when continuing requires changed intent or non-goals, a material outcome trade-off, an undelegated repository/account/audience, expanded privilege or credential access, destructive or irreversible shared effects, an enforced confirmation class, unsafe overlap with another owner's work, or a missing standard landing path.

#### Scenario: Routine step is mistaken for a choice

- **WHEN** an agent proposes asking whether to create a worktree, edit scoped files, run tests, commit, push the task branch, open or update its pull request, repair a causal failure, or follow an authorized landing path
- **THEN** the doctrine rejects the question as avoidable human attention

#### Scenario: Consequential boundary is crossed

- **WHEN** completion requires a new privilege, new external audience, destructive shared mutation, materially different outcome, or undelegated repository
- **THEN** the agent requests only the narrow decision required

### Requirement: Landing claims match effective authorization

Documentation SHALL distinguish an established repository landing path from an invented external action and SHALL NOT claim enforcement of green status, confirmation classes, or deployment authority without point-of-effect evidence.

#### Scenario: Repository declares merged-and-deployed

- **WHEN** repository policy names an established standard merge, deployment, and verification path
- **THEN** guidance treats that path as delegated while preserving every actually enforced safety condition

#### Scenario: Configuration is stored but not enforced

- **WHEN** a confirmation class or safety predicate is present in configuration but absent from point-of-effect enforcement
- **THEN** documentation labels it reserved or unenforced rather than describing it as a live gate
