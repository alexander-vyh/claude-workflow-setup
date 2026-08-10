# ADR-001: Use GitLab as Clean-Room Behavioral Prior Art

## Status

Accepted

## Context

GitLab publicly documents durable human checkpoints, typed workflow states,
pre-approved privileges, cross-client server-owned approval memory, and an attention
queue. Those behaviors closely match Escapement's problem. The relevant GitLab Duo
Workflow Service implementation is GitLab Enterprise Edition licensed and is coupled to
GitLab sessions, gateways, runners, databases, and product identity. Escapement must
remain host-neutral and distributable under its own terms.

## Decision

Escapement will independently specify and implement the useful public behaviors from
GitLab documentation and APIs. It will not copy, translate, vendor, or depend on GitLab
EE source code. Decision records and specs may cite public documentation as requirements
evidence; implementation work must use Escapement vocabulary, repository history,
fixtures, and tests.

## Consequences

The design benefits from mature product evidence without inheriting GitLab's license or
runtime. Clean-room provenance requires more explicit contracts and independent tests,
but those constraints also prevent accidental GitLab-specific architecture. Future
contributors must not treat visible EE source as an implementation shortcut.

## Public References

- GitLab Duo Agent Platform Flows API:
  <https://docs.gitlab.com/api/duo_agent_platform_flows/>
- GitLab Agent Platform sessions and human checkpoints:
  <https://docs.gitlab.com/user/duo_agent_platform/sessions/>
- GitLab tool governance:
  <https://docs.gitlab.com/user/duo_agent_platform/agents/tool-governance/>
- GitLab Duo Workflow Service license notice:
  <https://gitlab.com/gitlab-org/duo-workflow/duo-workflow-service/-/blob/main/LICENSE>
