#!/usr/bin/env python3
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import kb_evidence
import kb_quality_gate


def _entry(entry_id: str, *, repo: str, title: str = "same title") -> dict:
    return {
        "id": entry_id,
        "ts": "2026-07-02T00:00:00+08:00",
        "kind": "issue",
        "repo": repo,
        "branch": "main",
        "title": title,
        "story": "verified",
        "status": "current",
        "verified_at": "2026-07-02T00:01:00+08:00",
        "evidence_level": "primary",
        "authority": "current_evidence",
        "source_paths": ["evidence/app.log"],
    }


def _write(path: Path, rows: list[object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")


def test_cross_repo_same_title_is_allowed() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir) / "personal-kb" / "repos"
        evidence = Path(temp_dir) / "evidence" / "app.log"
        evidence.parent.mkdir(parents=True)
        evidence.write_text("ok", encoding="utf-8")
        _write(root / "repo-a" / "main" / "kb.jsonl", [_entry("a", repo="repo-a")])
        _write(root / "repo-b" / "main" / "kb.jsonl", [_entry("b", repo="repo-b")])
        result = kb_quality_gate.audit(root, keep_from="2026-07-01")
        assert result["ok"] is True, result


def test_invalid_json_and_non_object_rows_fail() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir) / "personal-kb" / "repos"
        path = root / "repo" / "main" / "kb.jsonl"
        path.parent.mkdir(parents=True)
        path.write_text('{"id":\n[1, 2, 3]\n', encoding="utf-8")
        result = kb_quality_gate.audit(root, keep_from="2026-07-01")
        issues = [item["issue"] for item in result["violations"]]
        assert any("invalid JSON" in issue for issue in issues)
        assert any("must be an object" in issue for issue in issues)
        assert result["non_empty_lines"] == 2


def test_each_record_requires_resolvable_evidence() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir) / "personal-kb" / "repos"
        broken = _entry("broken", repo="repo")
        broken["source_paths"] = ["missing/file.log"]
        invalid_ref = _entry("bad-ref", repo="repo", title="bad ref")
        invalid_ref["source_paths"] = []
        invalid_ref["evidence_refs"] = [{"type": "unknown", "value": "x"}]
        _write(root / "repo" / "main" / "kb.jsonl", [broken, invalid_ref])
        result = kb_quality_gate.audit(root, keep_from="2026-07-01")
        issues = [item["issue"] for item in result["violations"]]
        assert issues.count("no resolvable evidence for record") == 2
        assert "invalid evidence_refs structure" in issues


def test_relative_source_uses_entry_workspace() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        base = Path(temp_dir)
        root = base / "personal-kb" / "repos"
        workspace = base / "project-alpha"
        evidence = workspace / "evidence" / "app.log"
        evidence.parent.mkdir(parents=True)
        evidence.write_text("ok", encoding="utf-8")
        entry = _entry("workspace-relative", repo="repo", title="workspace relative")
        entry["workspace_dir"] = str(workspace)
        _write(root / "repo" / "main" / "kb.jsonl", [entry])

        result = kb_quality_gate.audit(root, keep_from="2026-07-01")
        assert result["ok"] is True, result
        assert result["source_resolved_rate"] == 1.0


def test_v2_revision_and_freshness_are_audited() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        base = Path(temp_dir)
        root = base / "personal-kb" / "repos"
        evidence = base / "evidence" / "app.log"
        evidence.parent.mkdir(parents=True)
        evidence.write_text("v1", encoding="utf-8")
        entry = _entry("v2", repo="repo", title="v2 evidence")
        entry["schema_version"] = 2
        entry["workspace_dir"] = str(base)
        entry["source_paths"] = ["evidence/app.log"]
        entry["evidence_snapshots"] = kb_evidence.capture_evidence_snapshots(entry, base)
        entry["record_rev"] = kb_evidence.canonical_entry_revision(entry)
        _write(root / "repo" / "main" / "kb.jsonl", [entry])

        assert kb_quality_gate.audit(root, keep_from="2026-07-01")["ok"] is True
        evidence.write_text("v2", encoding="utf-8")
        result = kb_quality_gate.audit(root, keep_from="2026-07-01")
        issues = [item["issue"] for item in result["violations"]]
        assert "evidence freshness is needs_recheck" in issues


def main() -> int:
    tests = [
        test_cross_repo_same_title_is_allowed,
        test_invalid_json_and_non_object_rows_fail,
        test_each_record_requires_resolvable_evidence,
        test_relative_source_uses_entry_workspace,
        test_v2_revision_and_freshness_are_audited,
    ]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print("kb_quality_gate tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
