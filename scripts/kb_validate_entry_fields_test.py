#!/usr/bin/env python3
from __future__ import annotations

from kb_lib import validate_entry_fields


def assert_valid(entry: dict) -> None:
    ok, error = validate_entry_fields(entry)
    assert ok, error


def assert_invalid(entry: dict, expected: str) -> None:
    ok, error = validate_entry_fields(entry)
    assert not ok, "expected validation failure"
    assert expected in error, f"expected {expected!r} in {error!r}"


def valid_config_entry() -> dict:
    return {
        "kind": "map",
        "title": "AI report base-url",
        "story": "背景/结论/验证",
        "aliases": ["AI报告服务", "ai.report.base-url"],
        "source_paths": ["config/application.yml"],
    }


def main() -> int:
    assert_valid({"kind": "experience", "title": "simple note"})
    assert_invalid({"type": "note", "title": "legacy note"}, "type 字段已废弃")
    assert_valid(valid_config_entry())

    missing_aliases = valid_config_entry()
    missing_aliases.pop("aliases")
    assert_valid(missing_aliases)

    one_alias = valid_config_entry()
    one_alias["aliases"] = ["AI报告服务"]
    assert_valid(one_alias)

    missing_evidence = valid_config_entry()
    missing_evidence.pop("source_paths")
    assert_valid(missing_evidence)

    missing_confidence = valid_config_entry()
    assert_valid(missing_confidence)

    invalid_confidence = valid_config_entry()
    invalid_confidence["confidence"] = 1.5
    assert_invalid(invalid_confidence, "0-1")

    invalid_project_specific = valid_config_entry()
    invalid_project_specific["project_specific"] = "demo"
    assert_invalid(invalid_project_specific, "project_specific")

    invalid_transferable = valid_config_entry()
    invalid_transferable["transferable"] = "demo"
    assert_invalid(invalid_transferable, "transferable")

    env_diff = {
        "kind": "pitfall",
        "title": "PowerShell UTF-8",
        "story": "环境差异记录",
        "aliases": ["PowerShell UTF-8", "OutputEncoding"],
        "source_paths": ["$PROFILE"],
        "confidence": 0.9,
    }
    assert_valid(env_diff)

    env_diff_missing_aliases = dict(env_diff)
    env_diff_missing_aliases.pop("aliases")
    assert_valid(env_diff_missing_aliases)

    print("validate_entry_fields tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
