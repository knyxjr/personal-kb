#!/usr/bin/env python3
from __future__ import annotations

import json
import base64
import os
import tempfile
from pathlib import Path

import kb_add
import kb_sensitive_scan
import kb_update


def test_scan_and_redact_credentials_without_echoing_values() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        path = Path(temp_dir) / "kb.jsonl"
        row = {
            "id": "entry-1",
            "story": "client_secret = very-secret-value-123 and Authorization: Bearer abcdefghijklmnop",
            "trigger_terms": ["token", "client_secret"],
        }
        path.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")

        dry_run = kb_sensitive_scan.scan_file(path, apply=False)
        assert dry_run["match_count"] == 2
        assert "very-secret-value-123" in path.read_text(encoding="utf-8")

        applied = kb_sensitive_scan.scan_file(path, apply=True)
        assert applied["match_count"] == 2
        rewritten = path.read_text(encoding="utf-8")
        assert "very-secret-value-123" not in rewritten
        assert "abcdefghijklmnop" not in rewritten
        assert rewritten.count(kb_sensitive_scan.REDACTED) == 2


def test_safe_placeholders_are_not_flagged() -> None:
    payload = {
        "story": "password=${DB_PASSWORD}; client_secret=[REDACTED]",
        "trigger_terms": ["token", "password"],
    }
    assert kb_sensitive_scan.sensitive_findings(payload) == []


def _entry_b64(payload: dict[str, object]) -> str:
    return base64.b64encode(json.dumps(payload, ensure_ascii=False).encode("utf-8")).decode("ascii")


def test_add_rejects_credential_shaped_content() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        previous_root = os.environ.get("PERSONAL_KB_ROOT")
        previous_cwd = Path.cwd()
        os.environ["PERSONAL_KB_ROOT"] = temp_dir
        os.chdir(temp_dir)
        try:
            rc = kb_add.main([
                "--entry-b64",
                _entry_b64({
                    "kind": "issue",
                    "title": "sensitive write",
                    "story": "client_secret=very-secret-value-123",
                }),
            ])
            assert rc == 4
            assert not list(Path(temp_dir).rglob("kb.jsonl"))
        finally:
            os.chdir(previous_cwd)
            if previous_root is None:
                os.environ.pop("PERSONAL_KB_ROOT", None)
            else:
                os.environ["PERSONAL_KB_ROOT"] = previous_root


def test_update_rejects_immutable_id_change() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        previous_root = os.environ.get("PERSONAL_KB_ROOT")
        previous_cwd = Path.cwd()
        os.environ["PERSONAL_KB_ROOT"] = temp_dir
        os.chdir(temp_dir)
        try:
            add_rc = kb_add.main([
                "--entry-b64",
                _entry_b64({
                    "kind": "issue",
                    "title": "immutable entry",
                    "story": "verified safe content",
                }),
            ])
            assert add_rc == 0
            rows = [
                json.loads(line)
                for path in Path(temp_dir).rglob("kb.jsonl")
                for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            assert len(rows) == 1
            rc = kb_update.main([
                "update",
                rows[0]["id"],
                "--entry-b64",
                _entry_b64({"id": "replacement-id"}),
            ])
            assert rc == 2
        finally:
            os.chdir(previous_cwd)
            if previous_root is None:
                os.environ.pop("PERSONAL_KB_ROOT", None)
            else:
                os.environ["PERSONAL_KB_ROOT"] = previous_root


def main() -> int:
    tests = [
        test_scan_and_redact_credentials_without_echoing_values,
        test_safe_placeholders_are_not_flagged,
        test_add_rejects_credential_shaped_content,
        test_update_rejects_immutable_id_change,
    ]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print("kb_sensitive_scan tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
