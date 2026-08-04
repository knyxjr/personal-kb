#!/usr/bin/env python3
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import kb_adoption


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")


def _read_jsonl(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_adoption_events_path_uses_repos_meta() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        repos = Path(temp_dir) / "repos"
        assert kb_adoption.adoption_events_path(repos) == repos / "_meta" / "adoption_events.jsonl"
        with patch.object(kb_adoption, "kb_base_dir", return_value=repos):
            assert kb_adoption.adoption_events_path() == repos / "_meta" / "adoption_events.jsonl"


def test_append_adoption_event_writes_runtime_event() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        repos = Path(temp_dir) / "repos"
        path = kb_adoption.adoption_events_path(repos)
        with (
            patch.object(kb_adoption, "adoption_events_path", return_value=path),
            patch.object(kb_adoption, "now_iso", return_value="2026-07-12T10:00:00+08:00"),
        ):
            event = kb_adoption.append_adoption_event(
                "entry-1",
                "FIX",
                "demo",
                "main",
                "event-1",
                session_id="session-1",
            )

        assert _read_jsonl(path) == [event]
        assert event == {
            "event": "kb_adoption",
            "event_id": "event-1",
            "entry_id": "entry-1",
            "effect": "fix",
            "repo": "demo",
            "branch": "main",
            "session_id": "session-1",
            "ts": "2026-07-12T10:00:00+08:00",
        }


def test_load_adoption_stats_deduplicates_and_locate_does_not_heat() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        repos = Path(temp_dir) / "repos"
        path = kb_adoption.adoption_events_path(repos)
        rows = [
            {
                "event": "kb_adoption",
                "event_id": "event-locate",
                "entry_id": "entry-1",
                "effect": "locate",
                "ts": "2026-07-12T12:00:00+08:00",
            },
            {
                "event": "kb_adoption",
                "event_id": "event-decide",
                "entry_id": "entry-1",
                "effect": "decide",
                "ts": "2026-07-12T09:00:00+08:00",
            },
            {
                "event": "kb_adoption",
                "event_id": "event-fix",
                "entry_id": "entry-1",
                "effect": "fix",
                "ts": "2026-07-12T02:00:00+00:00",
            },
            {
                "event": "kb_adoption",
                "event_id": "event-write",
                "entry_id": "entry-1",
                "effect": "write",
                "ts": "2026-07-12T11:00:00+08:00",
            },
            {
                "event": "kb_adoption",
                "event_id": "event-legacy",
                "entry_id": "entry-1",
                "effect": "legacy",
                "ts": "2026-07-12T11:30:00+08:00",
            },
            # A physical retry is allowed but must not add another logical use.
            {
                "event": "kb_adoption",
                "event_id": "event-fix",
                "entry_id": "entry-1",
                "effect": "fix",
                "ts": "2026-07-12T13:00:00+08:00",
            },
            {
                "event": "not-an-adoption",
                "event_id": "ignored-event",
                "entry_id": "entry-1",
                "effect": "fix",
                "ts": "2026-07-13T00:00:00+08:00",
            },
            {
                "event": "kb_adoption",
                "event_id": None,
                "entry_id": "entry-1",
                "effect": "fix",
                "ts": "2026-07-13T00:00:00+08:00",
            },
        ]
        _write_jsonl(path, rows)

        stats = kb_adoption.load_adoption_stats(repos)

        assert stats == {
            "entry-1": {
                "heated_count": 4,
                "last_used_ts": "2026-07-12T11:30:00+08:00",
                "effects": {"locate": 1, "decide": 1, "fix": 1, "write": 1, "legacy": 1},
            }
        }


def test_event_id_is_deduplicated_globally() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        repos = Path(temp_dir) / "repos"
        _write_jsonl(
            kb_adoption.adoption_events_path(repos),
            [
                {
                    "event": "kb_adoption",
                    "event_id": "same-event",
                    "entry_id": "entry-1",
                    "effect": "write",
                    "ts": "2026-07-12T09:00:00+08:00",
                },
                {
                    "event": "kb_adoption",
                    "event_id": "same-event",
                    "entry_id": "entry-2",
                    "effect": "write",
                    "ts": "2026-07-12T10:00:00+08:00",
                },
            ],
        )

        stats = kb_adoption.load_adoption_stats(repos)

        assert set(stats) == {"entry-1"}
        assert stats["entry-1"]["heated_count"] == 1


def test_effective_usage_adds_legacy_and_runtime_heat() -> None:
    stats = {
        "entry-1": {
            "heated_count": 4,
            "last_used_ts": "2026-07-12T11:30:00+08:00",
            "effects": {"locate": 1, "fix": 4},
        }
    }

    assert kb_adoption.effective_usage({"id": "entry-1", "used_count": 7}, stats) == 11
    assert kb_adoption.effective_usage({"id": "entry-1", "used_count": "2"}, stats) == 6
    assert kb_adoption.effective_usage({"id": "entry-2", "used_count": 3}, stats) == 3
    assert kb_adoption.effective_usage({"id": "entry-1", "used_count": -2}, stats) == 4


def test_append_rejects_invalid_identity_or_effect() -> None:
    invalid_calls = [
        ("", "fix", "event-1"),
        ("entry-1", "unknown", "event-1"),
        ("entry-1", "fix", ""),
    ]
    for entry_id, effect, event_id in invalid_calls:
        try:
            kb_adoption.append_adoption_event(entry_id, effect, "demo", "main", event_id)
        except ValueError:
            continue
        raise AssertionError((entry_id, effect, event_id))


def main() -> int:
    tests = [
        test_adoption_events_path_uses_repos_meta,
        test_append_adoption_event_writes_runtime_event,
        test_load_adoption_stats_deduplicates_and_locate_does_not_heat,
        test_event_id_is_deduplicated_globally,
        test_effective_usage_adds_legacy_and_runtime_heat,
        test_append_rejects_invalid_identity_or_effect,
    ]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print("kb_adoption tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
