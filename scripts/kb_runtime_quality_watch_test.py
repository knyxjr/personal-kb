#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

_ORIGINAL_PLATFORM = sys.platform
sys.platform = "test"
try:
    import kb_runtime_quality_watch
    import kb_session_brief
finally:
    sys.platform = _ORIGINAL_PLATFORM


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_quality_watch_records_closeout_issues_once() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        base_dir = Path(temp_dir) / "repos"
        closeout = kb_runtime_quality_watch.closeout_path(base_dir)
        state_path = Path(temp_dir) / "state.json"
        log_path = Path(temp_dir) / "quality.jsonl"
        now = datetime.now(timezone.utc)
        write_jsonl(
            closeout,
            [
                {
                    "event": "kb_closeout",
                    "ts": (now - timedelta(minutes=10)).isoformat(timespec="seconds"),
                    "repo": "demo",
                    "branch": "main",
                    "rag_calls": 0,
                    "hit_count": 2,
                    "used_entry_ids": [],
                    "written_entry_ids": [],
                    "updated_entry_ids": [],
                    "skipped_reason": "",
                }
            ],
        )

        summary = kb_runtime_quality_watch.process_once(
            base_dir=base_dir,
            state_path=state_path,
            log_path=log_path,
            keep_current=2,
            repair_session_briefs=False,
        )

        assert summary["observed_rows"] == 1
        rows = read_jsonl(log_path)
        assert len(rows) == 1
        assert rows[0]["status"] == "issue"
        assert "hit_count>0 but rag_calls<=0" in rows[0]["issues"]
        assert "missing skipped_reason for no-action closeout" in rows[0]["issues"]

        second = kb_runtime_quality_watch.process_once(
            base_dir=base_dir,
            state_path=state_path,
            log_path=log_path,
            keep_current=2,
            repair_session_briefs=False,
        )
        assert second["observed_rows"] == 0
        assert len(read_jsonl(log_path)) == 1


def test_quality_watch_can_repair_current_brief_drift() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        base_dir = Path(temp_dir) / "repos"
        brief_path = kb_runtime_quality_watch.session_briefs_path(base_dir)
        state_path = Path(temp_dir) / "state.json"
        log_path = Path(temp_dir) / "quality.jsonl"
        now = datetime.now(timezone.utc)

        rows = []
        for index in range(3):
            brief = kb_session_brief.build_brief(
                title=f"brief-{index}",
                summary=f"summary-{index}",
                repo="demo",
                branch="main",
                cwd=str(Path(temp_dir) / "workspace"),
                tags=["recent-session"],
                anchors=["timeout"],
                queries=["timeout"],
                used_entry_ids=[],
                written_entry_ids=[],
                updated_entry_ids=[],
                source="codex",
                session_id=f"s-{index}",
            )
            brief["ts"] = (now - timedelta(minutes=30 - index)).isoformat(timespec="seconds")
            rows.append(brief)
        write_jsonl(brief_path, rows)

        summary = kb_runtime_quality_watch.process_once(
            base_dir=base_dir,
            state_path=state_path,
            log_path=log_path,
            keep_current=1,
            repair_session_briefs=True,
        )

        assert summary["observed_rows"] == 3
        assert summary["repaired_groups"] >= 1
        repaired_rows = read_jsonl(brief_path)
        current_rows = [row for row in repaired_rows if kb_session_brief._is_current(row)]
        assert len(current_rows) == 1
        quality_rows = read_jsonl(log_path)
        assert any(row["event"] == "kb_runtime_quality_repair" for row in quality_rows)


def main() -> int:
    tests = [
        test_quality_watch_records_closeout_issues_once,
        test_quality_watch_can_repair_current_brief_drift,
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

    print("kb_runtime_quality_watch tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
