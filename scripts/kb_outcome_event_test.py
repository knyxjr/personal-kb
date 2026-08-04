#!/usr/bin/env python3
from __future__ import annotations

import contextlib
import io
import json
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import patch

import kb_outcome_event


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _receipt(*, retrieval_id: str = "retrieval-1", entry_id: str = "entry-1") -> dict[str, Any]:
    return {
        "schema": "personal-kb.retrieval-receipt/v1",
        "retrieval_id": retrieval_id,
        "query": "demo query for alpha component",
        "repo": "demo",
        "branch": "main",
        "scope_anchors": ["component:alpha"],
        "hits": [
            {
                "entry_id": entry_id,
                "record_rev": "rev-1",
                "freshness_state": "fresh",
            }
        ],
        "created_at": "2026-08-03T20:00:00+08:00",
    }


def _event_kwargs() -> dict[str, Any]:
    return {
        "event_id": "outcome-1",
        "retrieval_id": "retrieval-1",
        "entry_id": "entry-1",
        "application_target": "artifact.json#/memory_application/0",
        "expected_effect": "prevent recurrence",
        "actual_result": "acceptance passed",
        "recurrence": "not_observed",
        "user_verdict": "accepted",
        "evidence_paths": ["reports/check.json"],
    }


def test_outcome_event_requires_persisted_retrieval_and_hit() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        base_dir = Path(temp_dir) / "repos"
        try:
            kb_outcome_event.append_outcome_event(**_event_kwargs(), base_dir=base_dir)
        except ValueError as exc:
            assert "no persisted receipt" in str(exc)
        else:
            raise AssertionError("missing retrieval receipt was accepted")

        _write_jsonl(kb_outcome_event.retrieval_receipts_path(base_dir), [_receipt()])
        invalid = _event_kwargs()
        invalid["entry_id"] = "entry-missing"
        try:
            kb_outcome_event.append_outcome_event(**invalid, base_dir=base_dir)
        except ValueError as exc:
            assert "exactly once" in str(exc)
        else:
            raise AssertionError("entry outside the retrieval receipt was accepted")


def test_outcome_event_is_strict_and_idempotent() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        base_dir = Path(temp_dir) / "repos"
        _write_jsonl(kb_outcome_event.retrieval_receipts_path(base_dir), [_receipt()])
        first = kb_outcome_event.append_outcome_event(
            **_event_kwargs(),
            base_dir=base_dir,
            created_at="2026-08-03T20:05:00+08:00",
        )
        retried = kb_outcome_event.append_outcome_event(
            **_event_kwargs(),
            base_dir=base_dir,
            created_at="2026-08-03T20:06:00+08:00",
        )

        assert retried == first
        assert _read_jsonl(kb_outcome_event.outcome_events_path(base_dir)) == [first]
        assert first == {
            "schema": "personal-kb.outcome-event/v1",
            "event_id": "outcome-1",
            "retrieval_id": "retrieval-1",
            "entry_id": "entry-1",
            "repo": "demo",
            "branch": "main",
            "record_rev": "rev-1",
            "application_target": "artifact.json#/memory_application/0",
            "expected_effect": "prevent recurrence",
            "actual_result": "acceptance passed",
            "recurrence": "not_observed",
            "user_verdict": "accepted",
            "evidence_paths": ["reports/check.json"],
            "created_at": "2026-08-03T20:05:00+08:00",
        }

        conflicting = _event_kwargs()
        conflicting["actual_result"] = "acceptance failed"
        try:
            kb_outcome_event.append_outcome_event(
                **conflicting,
                base_dir=base_dir,
            )
        except kb_outcome_event.IdempotencyConflictError:
            pass
        else:
            raise AssertionError("conflicting event_id was accepted")


def test_outcome_event_rejects_noncanonical_receipt_and_empty_fields() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        base_dir = Path(temp_dir) / "repos"
        invalid_receipt = _receipt()
        invalid_receipt["query_groups"] = []
        _write_jsonl(
            kb_outcome_event.retrieval_receipts_path(base_dir),
            [invalid_receipt],
        )
        try:
            kb_outcome_event.append_outcome_event(**_event_kwargs(), base_dir=base_dir)
        except ValueError as exc:
            assert "does not match" in str(exc)
        else:
            raise AssertionError("noncanonical receipt was accepted")

        _write_jsonl(kb_outcome_event.retrieval_receipts_path(base_dir), [_receipt()])
        for field in (
            "application_target",
            "expected_effect",
            "actual_result",
            "recurrence",
            "user_verdict",
        ):
            invalid = _event_kwargs()
            invalid[field] = ""
            try:
                kb_outcome_event.append_outcome_event(**invalid, base_dir=base_dir)
            except ValueError:
                continue
            raise AssertionError(f"empty {field} was accepted")

        invalid = _event_kwargs()
        invalid["evidence_paths"] = []
        try:
            kb_outcome_event.append_outcome_event(**invalid, base_dir=base_dir)
        except ValueError:
            pass
        else:
            raise AssertionError("empty evidence_paths was accepted")

        for field, invalid_value in (
            ("recurrence", "maybe"),
            ("user_verdict", "looks-good"),
        ):
            invalid = _event_kwargs()
            invalid[field] = invalid_value
            try:
                kb_outcome_event.append_outcome_event(**invalid, base_dir=base_dir)
            except ValueError:
                pass
            else:
                raise AssertionError(f"invalid {field} choice was accepted")


def test_outcome_feedback_summarizes_verified_events() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        base_dir = Path(temp_dir) / "repos"
        _write_jsonl(kb_outcome_event.retrieval_receipts_path(base_dir), [_receipt()])
        kb_outcome_event.append_outcome_event(**_event_kwargs(), base_dir=base_dir)
        second = _event_kwargs()
        second.update(
            {
                "event_id": "outcome-2",
                "actual_result": "the failure recurred after review",
                "recurrence": "observed",
                "user_verdict": "rejected",
                "created_at": "2026-08-03T21:00:00+08:00",
            }
        )
        kb_outcome_event.append_outcome_event(**second, base_dir=base_dir)

        feedback = kb_outcome_event.outcome_feedback_for_entries(
            {
                ("demo", "main", "entry-1"),
                ("other", "main", "entry-1"),
                ("demo", "main", "missing-entry"),
            },
            base_dir=base_dir,
        )

        assert set(feedback) == {("demo", "main", "entry-1")}
        summary = feedback[("demo", "main", "entry-1")]
        assert summary["event_count"] == 2
        assert summary["accepted_count"] == 1
        assert summary["rejected_count"] == 1
        assert summary["recurrence_observed_count"] == 1
        assert summary["recurrence_not_observed_count"] == 1
        assert summary["last_event"]["event_id"] == "outcome-2"


def test_outcome_event_cli_records_repeated_evidence_paths_silently() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        base_dir = Path(temp_dir) / "repos"
        receipts_path = kb_outcome_event.retrieval_receipts_path(base_dir)
        events_path = kb_outcome_event.outcome_events_path(base_dir)
        _write_jsonl(receipts_path, [_receipt()])
        stdout = io.StringIO()
        with (
            patch.object(kb_outcome_event, "retrieval_receipts_path", return_value=receipts_path),
            patch.object(kb_outcome_event, "outcome_events_path", return_value=events_path),
            patch.object(kb_outcome_event, "now_iso", return_value="2026-08-03T20:05:00+08:00"),
            contextlib.redirect_stdout(stdout),
        ):
            rc = kb_outcome_event.main(
                [
                    "--event-id",
                    "outcome-1",
                    "--retrieval-id",
                    "retrieval-1",
                    "--entry-id",
                    "entry-1",
                    "--application-target",
                    "artifact.json#/memory_application/0",
                    "--expected-effect",
                    "prevent recurrence",
                    "--actual-result",
                    "acceptance passed",
                    "--recurrence",
                    "not_observed",
                    "--user-verdict",
                    "accepted",
                    "--evidence-path",
                    "reports/check.json",
                    "--evidence-path",
                    "reports/acceptance.json",
                ]
            )

        assert rc == 0
        assert stdout.getvalue() == ""
        assert _read_jsonl(events_path)[0]["evidence_paths"] == [
            "reports/check.json",
            "reports/acceptance.json",
        ]


def main() -> int:
    tests = [
        test_outcome_event_requires_persisted_retrieval_and_hit,
        test_outcome_event_is_strict_and_idempotent,
        test_outcome_event_rejects_noncanonical_receipt_and_empty_fields,
        test_outcome_feedback_summarizes_verified_events,
        test_outcome_event_cli_records_repeated_evidence_paths_silently,
    ]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print("kb_outcome_event tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
