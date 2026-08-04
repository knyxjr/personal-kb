from __future__ import annotations

DEFAULT_KIND = "experience"

VALID_KINDS = {
    "map",
    "experience",
    "pitfall",
    "issue",
    "requirement",
    "implementation",
}

LEGACY_TYPE_TO_KIND = {
    "api_endpoint": "map",
    "branch_risk": "pitfall",
    "bugfix": "issue",
    "config_file": "map",
    "database_schema": "map",
    "dependency": "pitfall",
    "dependency_lock": "pitfall",
    "deployment_procedure": "experience",
    "design_rationale": "implementation",
    "env_diff": "pitfall",
    "env_var": "map",
    "external_service_integration": "map",
    "feature_snapshot": "requirement",
    "feature_summary": "requirement",
    "feature_update": "requirement",
    "glossary": "map",
    "note": "requirement",
    "operation_manual": "experience",
    "permission_config": "pitfall",
    "project_feature_index": "requirement",
    "project_structure": "map",
    "sql_script": "map",
    "technical_change": "implementation",
    "technical_decision": "implementation",
    "topic_trace": "requirement",
}


def parse_kind_filter(value: str) -> list[str]:
    kinds = [item.strip() for item in (value or "").split(",") if item.strip()]
    invalid = [kind for kind in kinds if kind not in VALID_KINDS]
    if invalid:
        raise ValueError(
            "invalid kind: "
            + ", ".join(invalid)
            + "; valid kinds: "
            + ", ".join(sorted(VALID_KINDS))
        )
    return kinds


def parse_legacy_type_filter(value: str) -> list[str]:
    """Map deprecated --type filters to the 6-kind model.

    The storage model remains kind-only. This helper exists so stale AGENTS.md
    snippets and old runbooks fail less often while still producing kind filters.
    """
    values = [item.strip() for item in (value or "").split(",") if item.strip()]
    mapped: list[str] = []
    invalid: list[str] = []
    for item in values:
        if item in VALID_KINDS:
            kind = item
        else:
            kind = LEGACY_TYPE_TO_KIND.get(item)
        if not kind:
            invalid.append(item)
            continue
        if kind not in mapped:
            mapped.append(kind)
    if invalid:
        raise ValueError(
            "invalid legacy type: "
            + ", ".join(invalid)
            + "; valid kinds: "
            + ", ".join(sorted(VALID_KINDS))
            + "; known legacy types: "
            + ", ".join(sorted(LEGACY_TYPE_TO_KIND))
        )
    return mapped


def legacy_type_to_kind(value: str) -> str | None:
    item = (value or "").strip()
    if not item:
        return None
    if item in VALID_KINDS:
        return item
    return LEGACY_TYPE_TO_KIND.get(item)
