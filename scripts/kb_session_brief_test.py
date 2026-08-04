#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

_ORIGINAL_PLATFORM = sys.platform
sys.platform = "test"
try:
    import kb_session_brief
finally:
    sys.platform = _ORIGINAL_PLATFORM


@dataclass(frozen=True)
class TempContext:
    repo_name: str
    branch: str
    branch_dir: str
    repo_dir: Path
    branch_path: Path
    kb_path: Path
    summary_path: Path
    index_path: Path
    archive_dir: Path
    attachments_dir: Path
    workspace_dir: str


def make_context(root: Path) -> TempContext:
    branch_path = root / "repos" / "demo" / "main"
    return TempContext(
        repo_name="demo",
        branch="main",
        branch_dir="main",
        repo_dir=branch_path.parent,
        branch_path=branch_path,
        kb_path=branch_path / "kb.jsonl",
        summary_path=branch_path / "summary.jsonl",
        index_path=branch_path / "index.json",
        archive_dir=branch_path / "archive",
        attachments_dir=branch_path / "attachments",
        workspace_dir=str(root / "workspace"),
    )


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_append_brief_keeps_only_latest_current_rows() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        timestamps = [
            "2026-07-03T10:00:00+00:00",
            "2026-07-03T10:10:00+00:00",
            "2026-07-03T10:20:00+00:00",
        ]
        titles = ["brief-1", "brief-2", "brief-3"]

        with patch.object(kb_session_brief, "now_iso", side_effect=timestamps):
            for title in titles:
                brief = kb_session_brief.build_brief(
                    title=title,
                    summary=f"summary for {title}",
                    repo="demo",
                    branch="main",
                    cwd=str(root / "workspace"),
                    tags=["recent-session"],
                    anchors=["anchor"],
                    queries=["demo"],
                    used_entry_ids=[],
                    written_entry_ids=[],
                    updated_entry_ids=[],
                    source="codex",
                    session_id=title,
                )
                kb_session_brief.append_brief(brief, base_dir=root, keep_current=2)

        rows = read_jsonl(root / "_meta" / "session_briefs.jsonl")
        assert len(rows) == 3
        assert rows[0]["status"] == kb_session_brief.ROLLED_OFF_STATUS
        assert rows[1]["status"] == "current"
        assert rows[2]["status"] == "current"


def test_search_recent_briefs_filters_noncurrent_and_old_rows() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        ctx = make_context(root)
        now = datetime.now(timezone.utc)
        recent_current = kb_session_brief.build_brief(
            title="recent fix",
            summary="current task boundary and correction",
            repo="demo",
            branch="main",
            cwd=str(root / "workspace"),
            tags=["recent-session"],
            anchors=["timeout"],
            queries=["timeout"],
            used_entry_ids=[],
            written_entry_ids=[],
            updated_entry_ids=[],
            source="codex",
            session_id="s-3",
        )
        recent_current["ts"] = (now - timedelta(hours=3)).isoformat(timespec="seconds")

        recent_noncurrent = kb_session_brief.build_brief(
            title="rolled off",
            summary="should not be returned",
            repo="demo",
            branch="main",
            cwd=str(root / "workspace"),
            tags=["recent-session"],
            anchors=["timeout"],
            queries=["timeout"],
            used_entry_ids=[],
            written_entry_ids=[],
            updated_entry_ids=[],
            source="codex",
            session_id="s-2",
            status=kb_session_brief.ROLLED_OFF_STATUS,
        )
        recent_noncurrent["ts"] = (now - timedelta(hours=2)).isoformat(timespec="seconds")

        old_current = kb_session_brief.build_brief(
            title="too old",
            summary="older than window",
            repo="demo",
            branch="main",
            cwd=str(root / "workspace"),
            tags=["recent-session"],
            anchors=["timeout"],
            queries=["timeout"],
            used_entry_ids=[],
            written_entry_ids=[],
            updated_entry_ids=[],
            source="codex",
            session_id="s-1",
        )
        old_current["ts"] = (now - timedelta(days=3)).isoformat(timespec="seconds")

        path = root / "_meta" / "session_briefs.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in [old_current, recent_noncurrent, recent_current]),
            encoding="utf-8",
        )

        with (
            patch.object(kb_session_brief, "resolve_context", return_value=ctx),
            patch.object(kb_session_brief, "kb_base_dir", return_value=root),
        ):
            items = kb_session_brief.search_recent_briefs("timeout", recent_days=2, limit=5)

        assert [item["title"] for item in items] == ["recent fix"]
        assert items[0]["context_layer"] == "recent_session"


def main() -> int:
    tests = [
        test_append_brief_keeps_only_latest_current_rows,
        test_search_recent_briefs_filters_noncurrent_and_old_rows,
    ]
    failures: list[str] = []
    for test in tests:
        try:
            test()
            print(f"PASS {test.__name__}")
        except Exception as exc:
            failures.append(test.__name__)
            print(f"FAIL {test.__name__}: {type(exc).__name__}: {exc}")

    if failures:
        print(f"{len(failures)} failed: {', '.join(failures)}")
        return 1

    print("kb_session_brief tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
