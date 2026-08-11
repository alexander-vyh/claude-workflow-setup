## ADDED Requirements

### Requirement: Unresolved decisions are action-local

Shared Escapement guidance SHALL treat an unresolved question or approval as blocking only the action and dependent work that require it. Independent authorized work SHALL remain runnable.

#### Scenario: One branch needs human input

- **WHEN** one action cannot continue without a consequential human decision and another in-scope action is independent and authorized
- **THEN** the agent continues the independent action while preserving the unresolved decision

#### Scenario: No authorized work remains

- **WHEN** every remaining route to the delegated outcome depends on the unresolved decision
- **THEN** the session may report `input_required` with the single narrow decision needed

### Requirement: Informational interjections do not replace delegated work

Shared Escapement guidance SHALL answer a status question, informational side question, or additive instruction and then resume the active delegated workflow unless the user explicitly cancels, redirects, or replaces it.

#### Scenario: User asks a side question

- **WHEN** a user asks an informational question during active authorized work without replacing the original request
- **THEN** the agent answers it and resumes the original work without asking whether to continue

#### Scenario: User redirects the work

- **WHEN** the user explicitly cancels, replaces, or redirects the active request
- **THEN** the new direction supersedes the prior workflow

### Requirement: Adapter enforcement status is truthful

Client-specific surfaces SHALL distinguish mechanically enforced continuation from guidance-only behavior and SHALL NOT infer support from another client's hook model.

#### Scenario: Client lacks a lifecycle hook

- **WHEN** the installed client cannot intercept a final response or persist an action-local wait
- **THEN** its surface identifies the gap and relies on explicit durable work state without claiming native enforcement

#### Scenario: Client capability becomes available

- **WHEN** a client version exposes the required lifecycle primitive and a fixture proves the payload and behavior
- **THEN** the adapter may be marked mechanically enforced for that version and configuration
