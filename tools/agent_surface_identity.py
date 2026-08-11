"""Validate Escapement's canonical identity and support-claim surfaces.

This module deliberately owns semantic identity validation, while
``render_agent_surfaces.py`` remains responsible for distribution mechanics.
Keeping those responsibilities separate makes invalid input fail before the
renderer replaces any published plugin tree.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any


VALID_SUPPORT_CLAIM_STATUSES = {
    "unsupported",
    "reserved",
    "informational",
    "guidance-only",
}
REQUIRED_CAPABILITIES = (
    "Intent and authority",
    "Design and specification",
    "Executable dependency-aware work breakdown",
    "Capacity allocation",
    "Isolated execution",
    "Action-local continuation and repair",
    "Independent outcome verification",
    "Authorized landing and delivery",
    "Learning and feedback",
)
EXPECTED_ADAPTER_MAPPING = {
    "Design and specification": ["OpenSpec"],
    "Executable dependency-aware work breakdown": ["Beads"],
    "Isolated execution": ["Git worktrees"],
    "Capacity allocation": ["Claude Code", "Codex"],
    "Authorized landing and delivery": ["GitHub"],
}
CURRENT_ADAPTER_TOKENS = tuple(
    dict.fromkeys(
        tool
        for tools in EXPECTED_ADAPTER_MAPPING.values()
        for tool in tools
    )
)
FORBIDDEN_CORE_TOKENS = (
    *CURRENT_ADAPTER_TOKENS,
    "Git",
    "Pi",
    "OPA",
    "Cedar",
)
LEGACY_IDENTITY_FRAGMENTS = (
    "agentic workflow system built on top of",
    "host-neutral workflow layer",
    "OpenSpec, opinionated",
    "shared workflow for Claude Code and Codex",
    "workflow built on OpenSpec",
)
MISSION_SURFACES = (
    Path("agent-surfaces/onboarding/shared.md"),
    Path("README.md"),
    Path("docs/VOCABULARY.md"),
    Path("docs/NAMING.md"),
    Path("docs/deck.html"),
    Path("AGENTS.md"),
    Path("CLAUDE.md"),
    Path("plugins/escapement/.codex-plugin/plugin.json"),
    Path("plugins/escapement-claude/.claude-plugin/plugin.json"),
    Path(".claude-plugin/marketplace.json"),
)
CAPABILITY_SURFACES = (
    Path("agent-surfaces/identity.json"),
    Path("agent-surfaces/onboarding/shared.md"),
    Path("README.md"),
    Path("docs/deck.html"),
)
PUBLIC_IDENTITY_SURFACES = (
    Path("README.md"),
    Path("docs/VOCABULARY.md"),
    Path("docs/NAMING.md"),
    Path("docs/deck.html"),
)
CORE_IDENTITY_START = "<!-- escapement:core-identity:start -->"
CORE_IDENTITY_END = "<!-- escapement:core-identity:end -->"
CORE_IDENTITY_SURFACES = (
    Path("agent-surfaces/onboarding/shared.md"),
    *PUBLIC_IDENTITY_SURFACES,
    Path("AGENTS.md"),
    Path("CLAUDE.md"),
)
EXPECTED_SUPPORT_CLAIMS = {
    "merge-green-status": "unsupported",
    "confirm-class-enforcement": "reserved",
    "deploy-execution": "informational",
    "codex-final-response-interception": "guidance-only",
}
EXPECTED_SUPPORT_REASONS = {
    "merge-green-status": (
        "The merge authorization hook resolves repository-declared merge authority but "
        "does not observe pull-request check or green status."
    ),
    "confirm-class-enforcement": (
        "Repository confirmation classes are stored but are not currently enforced by "
        "the merge authorization hook."
    ),
    "deploy-execution": (
        "Repository deploy metadata is surfaced as outcome context and does not execute "
        "or independently authorize a deployment command."
    ),
    "codex-final-response-interception": (
        "The installed Codex adapter exposes no Stop or final-response hook; durable work "
        "state and SessionStart guidance support continuation without native interception."
    ),
}
SUPPORT_CLAIM_SURFACES = (
    Path("agent-surfaces/onboarding/authority.md"),
    Path("README.md"),
    Path("docs/VOCABULARY.md"),
    Path("docs/deck.html"),
    Path("AGENTS.md"),
    Path("CLAUDE.md"),
    Path("claude/rules/outcome-ownership.md"),
    Path("claude/rules/continuation-harness.md"),
)
SUPPORT_NARRATIVE_SURFACES = (
    *SUPPORT_CLAIM_SURFACES,
    Path("agent-surfaces/onboarding/hosts/codex.md"),
)
ADAPTER_MAPPING_START = "<!-- escapement:adapter-mapping:start -->"
ADAPTER_MAPPING_END = "<!-- escapement:adapter-mapping:end -->"
ADAPTER_MAPPING_SURFACES = (
    Path("agent-surfaces/onboarding/shared.md"),
    Path("README.md"),
    Path("AGENTS.md"),
    Path("CLAUDE.md"),
)
MUTABLE_COUNT_RE = re.compile(
    r"\b(?:\d+|zero|one|two|three|four|five|six|seven|eight|nine|ten|"
    r"eleven|twelve|forty-six)\s*(?:-|\s)?(?:step|steps|skill|skills|hook|hooks|"
    r"hook scripts|human gates)\b",
    re.IGNORECASE,
)
SUPPORT_REGION_START = "<!-- escapement:support-claims:start"
SUPPORT_REGION_END = "<!-- escapement:support-claims:end -->"
SUPPORT_REGION_RE = re.compile(
    re.escape(SUPPORT_REGION_START) + r"(?P<body>.*?)" + re.escape(SUPPORT_REGION_END),
    re.DOTALL,
)


def _expected_support_region_lines(support_reasons: dict[str, str]) -> list[str]:
    lines: list[str] = []
    for claim_id, status in EXPECTED_SUPPORT_CLAIMS.items():
        lines.extend(
            (
                f"{claim_id}={status}",
                f"{claim_id}-reason={support_reasons.get(claim_id, '')}",
            )
        )
    lines.append("-->")
    return lines


def _mapping_records(region: str) -> tuple[dict[str, list[str]], list[str]]:
    """Parse the two intentionally supported mapping renderings.

    Mapping regions contain only a compact Markdown table or bullet records.
    Explanatory prose belongs outside the region so the authority mapping can be
    compared as an exact normalized set rather than inferred from English.
    """

    records: dict[str, list[str]] = {}
    errors: list[str] = []
    for raw_line in region.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line == "| Durable capability | Current adapter |" or re.fullmatch(
            r"\|\s*:?-+:?\s*\|\s*:?-+:?\s*\|", line
        ):
            continue
        if line.startswith("-"):
            line = line[1:].strip()
        if line.startswith("|") and line.endswith("|"):
            line = line[1:-1].strip()
        cells = [cell.strip().replace("**", "") for cell in line.split("|")]
        if len(cells) != 2:
            errors.append(f"unrecognized mapping line {raw_line.strip()!r}")
            continue
        capability, raw_tools = cells
        if capability in records:
            errors.append(f"duplicate mapping for {capability}")
            continue
        records[capability] = [tool.strip() for tool in raw_tools.split(",") if tool.strip()]
    return records, errors


def _adapter_token_pattern(token: str) -> re.Pattern[str]:
    return re.compile(rf"(?<![A-Za-z0-9_]){re.escape(token)}(?![A-Za-z0-9_])", re.IGNORECASE)


def _flatten_core_field(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(item for item in value if isinstance(item, str))
    return ""


def _ordered_fragments_present(text: str, fragments: tuple[str, ...]) -> bool:
    cursor = -1
    for fragment in fragments:
        position = text.find(fragment, cursor + 1)
        if position <= cursor:
            return False
        cursor = position
    return True


def _semantic_units(prose: str) -> list[str]:
    """Return authored prose units while excluding paths, code, URLs, and markup.

    The claim validator reasons about human-facing assertions, not adapter names
    that merely occur in installation commands or repository paths. Wrapped prose
    lines are joined before sentence splitting so a limitation cannot be mistaken
    for a standalone affirmative fragment.
    """

    prose = re.sub(r"<pre\b.*?</pre>", "\n", prose, flags=re.DOTALL | re.IGNORECASE)
    prose = re.sub(r"```.*?```", "\n", prose, flags=re.DOTALL)
    prose = re.sub(r"`[^`]*`", "", prose)
    prose = re.sub(r"https?://\S+", "", prose)
    prose = re.sub(r"<[^>]+>", "\n", prose)
    prose = re.sub(r"(?<=[A-Za-z0-9,])\n(?=[a-z])", " ", prose)
    return [
        unit.strip()
        for unit in re.split(r"(?<=[.!?])\s+|\n+", prose)
        if unit.strip()
    ]


def _false_support_claim(sentence: str) -> bool:
    """Recognize assertions about mechanics reserved to structured support regions.

    Polarity is deliberately irrelevant here. A sentence such as "does not merely
    report red CI; it prevents merge" contains a truthful-looking negation wrapped
    around a false effect. Support assertions belong in manifest-bound regions so
    their status and reason can be checked together.
    """

    normalized = " ".join(sentence.lower().split())
    if not normalized:
        return False
    merge_topic = any(topic in normalized for topic in ("merge", "pull request", "landing"))
    merge_subject = any(
        subject in normalized
        for subject in (
            "escapement",
            "merge gate",
            "merge authorization",
            "merge hook",
            "repository policy",
        )
    )
    merge_evidence = bool(re.search(r"\b(?:green|red|ci)\b", normalized)) or any(
        evidence in normalized
        for evidence in (
            "required check",
            "check successful",
            "pr status",
            "pull request status",
        )
    )
    merge_effect = any(
        effect in normalized
        for effect in (
            "reject",
            "refuse",
            "prevent",
            "block",
            "require",
            "unless",
            "allow",
            "permit",
            "eligible",
            "verify",
            "check",
            "report",
        )
    )
    merge_is_explicit_limitation = any(
        limitation in normalized
        for limitation in (
            "does not observe",
            "does not itself observe",
            "not pr check status",
            "not pull-request check status",
            "unsupported",
        )
    ) and not any(
        affirmative in normalized
        for affirmative in ("prevents", "blocks", "enforces", "refuses", "requires")
    )
    if (
        merge_topic
        and merge_subject
        and merge_evidence
        and merge_effect
        and not merge_is_explicit_limitation
    ):
        return True

    confirm_topic = any(
        topic in normalized
        for topic in ("confirm_class", "confirmation class", "confirmation category")
    )
    confirm_scope = "confirm_class" in normalized or bool(
        re.search(
            r"\b(?:every|configured|stored|repository|non-empty)\s+"
            r"(?:\w+\s+){0,2}confirmation (?:class|category)\b",
            normalized,
        )
    )
    confirm_effect_text = normalized.replace("confirm_class", "")
    confirm_effect = bool(
        re.search(
            r"\b(?:demand|honor|ask|confirm|required|enforce|block|prevent)\w*\b",
            confirm_effect_text,
        )
    ) or "before merge" in confirm_effect_text
    confirm_is_explicit_limitation = any(
        limitation in normalized
        for limitation in (
            "not currently enforced",
            "is not enforced",
            "does not enforce",
            "reserved and not",
            "reserved configuration",
        )
    ) and not any(
        affirmative in normalized
        for affirmative in ("demand", "honor", "required before", "blocks", "prevents")
    )
    if confirm_topic and confirm_scope and confirm_effect and not confirm_is_explicit_limitation:
        return True

    deploy_topic = any(
        topic in normalized
        for topic in (
            "deploy metadata",
            "deployment metadata",
            "deployment configuration",
            "repository policy",
            "repository declaration",
        )
    )
    deploy_effect = any(
        effect in normalized
        for effect in (
            "command",
            "release",
            "launch",
            "execute",
            "run",
            "trigger",
            "automatically",
        )
    )
    deploy_is_explicit_limitation = any(
        limitation in normalized
        for limitation in (
            "does not execute",
            "does not itself execute",
            "neither executes",
            "is informational",
            "metadata is informational",
        )
    ) and not any(
        affirmative in normalized
        for affirmative in ("but executes", "then executes", "launches", "runs automatically")
    )
    if deploy_topic and deploy_effect and not deploy_is_explicit_limitation:
        return True

    codex_topic = "codex" in normalized and any(
        topic in normalized
        for topic in (
            "final response",
            "final-response",
            "final answer",
            "final reply",
            "reply",
            "concludes",
            "end the session",
            "stop",
        )
    )
    codex_effect = any(
        effect in normalized
        for effect in (
            "intercept",
            "catch",
            "force",
            "prevent",
            "block",
            "reject",
            "resume",
            "reopen",
        )
    )
    codex_is_explicit_limitation = any(
        limitation in normalized
        for limitation in (
            "no stop",
            "no final-response",
            "no final response",
            "cannot mechanically intercept",
            "guidance-only",
        )
    ) and not any(
        affirmative in normalized
        for affirmative in ("but resumes", "then resumes", "automatically resumes")
    )
    return codex_topic and codex_effect and not codex_is_explicit_limitation


def _semantic_false_support_claims(text: str) -> list[str]:
    prose = SUPPORT_REGION_RE.sub("", text)
    return [unit for unit in _semantic_units(prose) if _false_support_claim(unit)]


def _defines_core_through_adapter(sentence: str) -> bool:
    """Reject public product definitions that promote a current adapter into the core."""

    normalized = " ".join(sentence.lower().split())
    adapters = [
        token.lower()
        for token in FORBIDDEN_CORE_TOKENS
        if _adapter_token_pattern(token).search(normalized)
    ]
    if not adapters:
        return False
    explicit_adapter_limitation = any(
        limitation in normalized
        for limitation in (
            "replaceable adapter",
            "replaceable current",
            "current adapters, not",
            "not permanent product identity",
            "do not define the system",
            "does not define the system",
            "non-defining",
        )
    ) and not any(
        affirmative in normalized
        for affirmative in (
            "defines the product",
            "defines the core",
            "is the foundation",
            "is the engine",
            "at escapement's heart",
            "indispensable",
            "built on",
            "based on",
            "owns the workflow",
            "governs the workflow",
        )
    )
    if explicit_adapter_limitation:
        return False
    core_subject_present = any(
        subject in normalized
        for subject in ("escapement", "product", "system", "workflow", "core", "identity")
    )
    core_authority_present = any(
        marker in normalized
        for marker in (
            "core",
            "identity",
            "heart",
            "engine",
            "foundation",
            "fundamental",
            "built on",
            "based on",
            "indispensable",
            "without",
            "defines",
            "governs",
            "owns",
        )
    )
    if core_subject_present and core_authority_present:
        return True

    subject = r"(?:escapement|the product|the system|the workflow|the core|product identity)"
    adapter = "(?:" + "|".join(re.escape(token) for token in adapters) + ")"
    patterns = (
        rf"\b{subject}\b.{{0,90}}\b(?:fundamentally|essentially|inherently)?\s*"
        rf"(?:is\s+)?(?:built|based|founded|dependent)\s+(?:on|upon)\b.{{0,70}}\b{adapter}\b",
        rf"\b{adapter}\b.{{0,90}}\b(?:defines?|owns?|governs?|controls?)\b.{{0,70}}"
        rf"\b(?:the\s+)?(?:product|system|workflow|core|identity)\b",
        rf"\b{adapter}\b.{{0,60}}\b(?:is|are)\b.{{0,30}}\b(?:foundation|core)\b"
        rf".{{0,40}}\b(?:escapement|product|system|workflow)\b",
    )
    return any(re.search(pattern, normalized) for pattern in patterns)


def _semantic_core_definition_claims(text: str) -> list[str]:
    prose = re.sub(
        re.escape(ADAPTER_MAPPING_START) + r".*?" + re.escape(ADAPTER_MAPPING_END),
        "",
        text,
        flags=re.DOTALL,
    )
    return [unit for unit in _semantic_units(prose) if _defines_core_through_adapter(unit)]


def validate_canonical_identity(identity: dict[str, Any]) -> list[str]:
    """Validate the approved intrinsic semantics used by the mutating publisher."""

    errors = validate_renderable_identity(identity)
    identity_path = "agent-surfaces/identity.json"
    if tuple(identity.get("capabilities") or ()) != REQUIRED_CAPABILITIES:
        errors.append(
            f"{identity_path}: capabilities must match the required ordered capability chain"
        )
    if identity.get("current_adapters") != EXPECTED_ADAPTER_MAPPING:
        errors.append(f"{identity_path}: current_adapters must match the current capability mapping")
    return errors


def validate_support_manifest(manifest: dict[str, Any]) -> list[str]:
    """Validate support evidence before any generated surface is replaced."""

    errors: list[str] = []
    support_claims = manifest.get("support_claims")
    if not isinstance(support_claims, list):
        return ["agent-surfaces/manifest.json: support_claims must be a list"]

    actual_claims: dict[str, str] = {}
    seen_claim_ids: set[str] = set()
    for claim in support_claims:
        if not isinstance(claim, dict):
            errors.append("agent-surfaces/manifest.json: support claim must be an object")
            continue
        claim_id = claim.get("id")
        status = claim.get("status")
        reason = claim.get("reason")
        if not isinstance(claim_id, str) or not claim_id:
            errors.append("agent-surfaces/manifest.json: support claim missing id")
            continue
        if claim_id in seen_claim_ids:
            errors.append(
                f"agent-surfaces/manifest.json: duplicate support claim {claim_id}"
            )
            continue
        seen_claim_ids.add(claim_id)
        if status not in VALID_SUPPORT_CLAIM_STATUSES:
            errors.append(
                f"agent-surfaces/manifest.json: support claim {claim_id} "
                f"has invalid status {status!r}"
            )
        if not isinstance(reason, str) or not reason.strip():
            errors.append(
                f"agent-surfaces/manifest.json: support claim {claim_id} has no evidence reason"
            )
        elif reason != EXPECTED_SUPPORT_REASONS.get(claim_id):
            errors.append(
                f"agent-surfaces/manifest.json: support claim {claim_id} reason does not "
                "match point-of-effect evidence"
            )
        actual_claims[claim_id] = status
    if actual_claims != EXPECTED_SUPPORT_CLAIMS:
        errors.append(
            "agent-surfaces/manifest.json: support claims do not match current "
            "point-of-effect evidence"
        )
    return errors


def validate_renderable_identity(identity: dict[str, Any]) -> list[str]:
    """Validate inputs required to prepare generated targets without mutation."""

    errors: list[str] = []
    identity_path = "agent-surfaces/identity.json"
    if identity.get("version") != 1:
        errors.append(f"{identity_path}: version must be 1")

    for field in ("mission", "short_description"):
        value = identity.get(field)
        if not isinstance(value, str) or not value.strip():
            errors.append(f"{identity_path}: {field} must be a non-empty string")

    for field in ("operating_model", "capabilities", "principles"):
        value = identity.get(field)
        if not isinstance(value, list) or not value or not all(
            isinstance(item, str) and item.strip() for item in value
        ):
            errors.append(f"{identity_path}: {field} must be a non-empty string list")

    adapters = identity.get("current_adapters")
    if not isinstance(adapters, dict) or not adapters:
        errors.append(f"{identity_path}: current_adapters must be a non-empty object")
    elif not all(
        isinstance(capability, str)
        and capability
        and isinstance(tools, list)
        and tools
        and all(isinstance(tool, str) and tool for tool in tools)
        for capability, tools in adapters.items()
    ):
        errors.append(f"{identity_path}: current_adapters must map capabilities to tool lists")

    core = "\n".join(
        _flatten_core_field(identity.get(field))
        for field in ("mission", "short_description", "operating_model", "principles")
    )
    for token in FORBIDDEN_CORE_TOKENS:
        if _adapter_token_pattern(token).search(core):
            errors.append(f"{identity_path}: core identity names replaceable adapter {token}")
    return errors


def validate_identity_surfaces(
    root: Path,
    manifest: dict[str, Any],
    identity: dict[str, Any],
    surface_overrides: dict[Path, str] | None = None,
) -> list[str]:
    """Validate canonical semantics and authored/generated narrative parity."""

    errors = validate_canonical_identity(identity)
    mission = identity.get("mission") if isinstance(identity.get("mission"), str) else ""
    overrides = surface_overrides or {}

    def read_surface(relative: Path) -> str | None:
        if relative in overrides:
            return overrides[relative]
        path = root / relative
        if not path.is_file():
            return None
        return path.read_text(encoding="utf-8", errors="replace")

    errors.extend(validate_support_manifest(manifest))
    support_reasons = {
        claim["id"]: claim["reason"]
        for claim in manifest.get("support_claims", [])
        if isinstance(claim, dict)
        and isinstance(claim.get("id"), str)
        and isinstance(claim.get("reason"), str)
    }

    for relative in MISSION_SURFACES:
        text = read_surface(relative)
        if text is None:
            errors.append(f"{relative.as_posix()}: identity-bearing surface is missing")
            continue
        if mission and mission not in text:
            errors.append(f"{relative.as_posix()}: canonical mission is missing or divergent")
        lowered = text.lower()
        for fragment in LEGACY_IDENTITY_FRAGMENTS:
            if fragment.lower() in lowered:
                errors.append(f"{relative.as_posix()}: retains legacy identity fragment {fragment!r}")

    for relative in CAPABILITY_SURFACES:
        text = read_surface(relative)
        if text is None:
            errors.append(f"{relative.as_posix()}: capability surface is missing")
            continue
        if not _ordered_fragments_present(text, REQUIRED_CAPABILITIES):
            errors.append(f"{relative.as_posix()}: required capability chain is missing or reordered")

    for relative in ADAPTER_MAPPING_SURFACES:
        text = read_surface(relative)
        if text is None:
            errors.append(f"{relative.as_posix()}: adapter-mapping surface is missing")
            continue
        if ADAPTER_MAPPING_START not in text or ADAPTER_MAPPING_END not in text:
            errors.append(f"{relative.as_posix()}: structured adapter-mapping region is missing")
            continue
        if text.count(ADAPTER_MAPPING_START) != 1 or text.count(ADAPTER_MAPPING_END) != 1:
            errors.append(f"{relative.as_posix()}: adapter mapping must have one region")
            continue
        region = text.split(ADAPTER_MAPPING_START, 1)[1].split(ADAPTER_MAPPING_END, 1)[0]
        records, parse_errors = _mapping_records(region)
        if parse_errors or records != EXPECTED_ADAPTER_MAPPING:
            detail = "; ".join(parse_errors) if parse_errors else "mapping records differ"
            errors.append(
                f"{relative.as_posix()}: adapter mapping is not the exact current mapping: "
                f"{detail}"
            )

    for relative in SUPPORT_CLAIM_SURFACES:
        text = read_surface(relative)
        if text is None:
            errors.append(f"{relative.as_posix()}: support-claim surface is missing")
            continue
        regions = list(SUPPORT_REGION_RE.finditer(text))
        if len(regions) != 1:
            errors.append(
                f"{relative.as_posix()}: support claims must have exactly one structured region"
            )
        expected_lines = _expected_support_region_lines(support_reasons)
        for region in regions:
            actual_lines = [line.strip() for line in region.group("body").splitlines() if line.strip()]
            if actual_lines != expected_lines:
                errors.append(
                    f"{relative.as_posix()}: support-claim region must exactly match the "
                    "manifest-derived status and reasons"
                )

    for relative in SUPPORT_NARRATIVE_SURFACES:
        text = read_surface(relative)
        if text is None:
            errors.append(f"{relative.as_posix()}: support narrative surface is missing")
            continue
        for false_claim in _semantic_false_support_claims(text):
            errors.append(
                f"{relative.as_posix()}: contains false support claim: {false_claim[:120]}"
            )

    for relative in CORE_IDENTITY_SURFACES:
        text = read_surface(relative)
        if text is None:
            errors.append(f"{relative.as_posix()}: core-identity surface is missing")
            continue
        if text.count(CORE_IDENTITY_START) != 1 or text.count(CORE_IDENTITY_END) != 1:
            errors.append(f"{relative.as_posix()}: core identity must have one explicit region")
            continue
        core_region = text.split(CORE_IDENTITY_START, 1)[1].split(CORE_IDENTITY_END, 1)[0]
        if mission and mission not in core_region:
            errors.append(f"{relative.as_posix()}: canonical mission is outside core identity")
        for token in FORBIDDEN_CORE_TOKENS:
            if _adapter_token_pattern(token).search(core_region):
                errors.append(
                    f"{relative.as_posix()}: core identity names replaceable adapter {token}"
                )

    for relative in PUBLIC_IDENTITY_SURFACES:
        text = read_surface(relative)
        if text is None:
            errors.append(f"{relative.as_posix()}: public identity surface is missing")
            continue
        for false_claim in _semantic_core_definition_claims(text):
            errors.append(
                f"{relative.as_posix()}: defines the core through a replaceable adapter: "
                f"{false_claim[:120]}"
            )
        count = MUTABLE_COUNT_RE.search(text)
        if count:
            errors.append(
                f"{relative.as_posix()}: contains hand-maintained inventory count "
                f"{count.group(0)!r}"
            )

    return errors
