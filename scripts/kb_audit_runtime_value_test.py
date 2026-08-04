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
    import kb_audit_runtime_value
finally:
    sys.platform = _ORIGINAL_PLATFORM


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")


def test_runtime_value_report_counts_recent_closeouts() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        path = Path(temp_dir) / "closeout.jsonl"
        outcomes_path = Path(temp_dir) / "outcome_events.jsonl"
        now = datetime.now(timezone.utc)
        write_jsonl(
            path,
            [
                {
                    "event": "kb_closeout",
                    "ts": (now - timedelta(hours=3)).isoformat(timespec="seconds"),
                    "hit_count": 0,
                    "used_entry_ids": [],
                    "written_entry_ids": [],
                    "updated_entry_ids": [],
                },
                {
                    "event": "kb_closeout",
                    "ts": (now - timedelta(hours=2)).isoformat(timespec="seconds"),
                    "hit_count": 2,
                    "used_entry_ids": [],
                    "written_entry_ids": ["entry-new"],
                    "updated_entry_ids": [],
                    "session_brief_hit": True,
                    "session_brief_help": False,
                },
                {
                    "event": "kb_closeout",
                    "ts": (now - timedelta(hours=1)).isoformat(timespec="seconds"),
                    "hit_count": 3,
                    "used_entry_ids": ["entry-1"],
                    "written_entry_ids": [],
                    "updated_entry_ids": ["entry-1"],
                    "session_brief_hit": True,
                    "session_brief_help": True,
                },
                {
                    "event": "kb_closeout",
                    "ts": (now - timedelta(days=9)).isoformat(timespec="seconds"),
                    "hit_count": 5,
                    "used_entry_ids": ["old-entry"],
                    "written_entry_ids": [],
                    "updated_entry_ids": [],
                },
            ],
        )
        write_jsonl(
            outcomes_path,
            [
                {
                    "schema": "personal-kb.outcome-event/v1",
                    "created_at": (now - timedelta(minutes=50)).isoformat(timespec="seconds"),
                    "user_verdict": "accepted",
                    "recurrence": "not_observed",
                },
                {
                    "schema": "personal-kb.outcome-event/v1",
                    "created_at": (now - timedelta(minutes=40)).isoformat(timespec="seconds"),
                    "user_verdict": "rejected",
                    "recurrence": "observed",
                },
                {
                    "schema": "personal-kb.outcome-event/v1",
                    "created_at": (now - timedelta(minutes=30)).isoformat(timespec="seconds"),
                    "user_verdict": "not_provided",
                    "recurrence": "unknown",
                },
                {
                    "schema": "personal-kb.outcome-event/v1",
                    "created_at": (now - timedelta(days=9)).isoformat(timespec="seconds"),
                    "user_verdict": "accepted",
                    "recurrence": "not_observed",
                },
            ],
        )

        report = kb_audit_runtime_value.build_report(
            path=path,
            outcomes_path=outcomes_path,
            last_days=7,
        )

        assert report["closeout_total"] == 3
        assert report["hit_closeouts"] == 2
        assert report["adopted_closeouts"] == 1
        assert report["adopted_hit_closeouts"] == 1
        assert report["use_rate"] == 0.5
        assert report["no_hit_closeouts"] == 1
        assert report["hit_not_adopted_closeouts"] == 1
        assert report["written_closeouts"] == 1
        assert report["updated_closeouts"] == 1
        assert report["session_brief_hit_closeouts"] == 2
        assert report["session_brief_help_closeouts"] == 1
        assert report["session_brief_help_rate"] == 0.5
        assert report["outcome_event_total"] == 3
        assert report["accepted_outcomes"] == 1
        assert report["rejected_outcomes"] == 1
        assert report["user_acceptance_rate"] == 0.5
        assert report["recurrence_observed_outcomes"] == 1
        assert report["recurrence_not_observed_outcomes"] == 1
        assert report["recurrence_rate"] == 0.5


def main() -> int:
    tests = [test_runtime_value_report_counts_recent_closeouts]
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

    print("kb_audit_runtime_value tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
