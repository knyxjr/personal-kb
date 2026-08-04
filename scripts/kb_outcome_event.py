#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

from kb_lib import (
    IdempotencyConflictError,
    JsonlSafetyError,
    kb_base_dir,
    now_iso,
    persist_idempotent_jsonl_record,
    read_jsonl,
    validate_scope_anchor_bindings,
)


RETRIEVAL_RECEIPT_SCHEMA = "personal-kb.retrieval-receipt/v1"
OUTCOME_EVENT_SCHEMA = "personal-kb.outcome-event/v1"
OPAQUE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
RECURRENCE_VALUES = frozenset({"observed", "not_observed", "unknown", "not_applicable"})
USER_VERDICT_VALUES = frozenset({"accepted", "rejected", "mixed", "not_provided"})
RECEIPT_FIELDS = frozenset(
    {
        "schema",
        "retrieval_id",
        "query",
        "repo",
        "branch",
        "scope_anchors",
        "hits",
        "created_at",
    }
)
RECEIPT_HIT_FIELDS = frozenset({"entry_id", "record_rev", "freshness_state"})


def retrieval_receipts_path(base_dir: Path | None = None) -> Path:
    base = Path(base_dir) if base_dir is not None else kb_base_dir()
    return base / "_meta" / "retrieval_receipts.jsonl"


def outcome_events_path(base_dir: Path | None = None) -> Path:
    base = Path(base_dir) if base_dir is not None else kb_base_dir()
    return base / "_meta" / "outcome_events.jsonl"


def _required_text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value.strip()


def _opaque_id(value: Any, label: str) -> str:
    candidate = _required_text(value, label)
    if not OPAQUE_ID_RE.fullmatch(candidate):
        raise ValueError(
            f"{label} must be 1-128 opaque characters using letters, digits, '.', '_', ':', or '-'"
        )
    return candidate


def _evidence_paths(values: list[str] | tuple[str, ...] | None) -> list[str]:
    paths: list[str] = []
    for raw in values or []:
        value = _required_text(raw, "evidence_path")
        if value not in paths:
            paths.append(value)
    if not paths:
        raise ValueError("at least one evidence_path is required")
    return paths


def _choice(value: Any, label: str, allowed: frozenset[str]) -> str:
    normalized = _required_text(value, label).lower()
    if normalized not in allowed:
        choices = ", ".join(sorted(allowed))
        raise ValueError(f"{label} must be one of: {choices}")
    return normalized


def _receipt_semantic(receipt: dict[str, Any]) -> str:
    payload = {key: value for key, value in receipt.items() if key != "created_at"}
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _validated_receipt_hit(receipt: dict[str, Any], entry_id: str) -> dict[str, str]:
    if set(receipt) != RECEIPT_FIELDS:
        raise ValueError("retrieval receipt does not match personal-kb.retrieval-receipt/v1")
    if receipt.get("schema") != RETRIEVAL_RECEIPT_SCHEMA:
        raise ValueError("retrieval receipt has an unsupported schema")
    _opaque_id(receipt.get("retrieval_id"), "receipt retrieval_id")
    _required_text(receipt.get("query"), "receipt query")
    for field in ("repo", "branch", "created_at"):
        if not isinstance(receipt.get(field), str):
            raise ValueError(f"retrieval receipt {field} must be a string")

    anchors = receipt.get("scope_anchors")
    if not isinstance(anchors, list):
        raise ValueError("retrieval receipt scope_anchors must be a list")
    normalized_anchors = [_required_text(anchor, "receipt scope anchor") for anchor in anchors]
    if len(set(normalized_anchors)) != len(normalized_anchors):
        raise ValueError("retrieval receipt scope_anchors must be unique")
    validate_scope_anchor_bindings(str(receipt.get("query") or ""), normalized_anchors)
    hits = receipt.get("hits")
    if not isinstance(hits, list):
        raise ValueError("retrieval receipt hits must be a list")

    matched: list[dict[str, str]] = []
    seen_entry_ids: set[str] = set()
    for hit in hits:
        if not isinstance(hit, dict) or set(hit) != RECEIPT_HIT_FIELDS:
            raise ValueError("retrieval receipt hit has an invalid shape")
        normalized = {
            "entry_id": _required_text(hit.get("entry_id"), "receipt hit entry_id"),
            "record_rev": _required_text(hit.get("record_rev"), "receipt hit record_rev"),
            "freshness_state": _required_text(
                hit.get("freshness_state"),
                "receipt hit freshness_state",
            ),
        }
        if normalized["entry_id"] in seen_entry_ids:
            raise ValueError("retrieval receipt hit entry_id values must be unique")
        seen_entry_ids.add(normalized["entry_id"])
        if normalized["entry_id"] == entry_id:
            matched.append(normalized)

    if len(matched) != 1:
        raise ValueError(
            f"entry_id '{entry_id}' must appear exactly once in the linked retrieval receipt"
        )
    return matched[0]


def load_linked_receipt(
    retrieval_id: str,
    entry_id: str,
    *,
    base_dir: Path | None = None,
) -> tuple[dict[str, Any], dict[str, str]]:
    normalized_retrieval_id = _opaque_id(retrieval_id, "retrieval_id")
    normalized_entry_id = _required_text(entry_id, "entry_id")
    matches = [
        row
        for row in read_jsonl(retrieval_receipts_path(base_dir))
        if row.get("retrieval_id") == normalized_retrieval_id
    ]
    if not matches:
        raise ValueError(f"retrieval_id '{normalized_retrieval_id}' has no persisted receipt")

    semantic = _receipt_semantic(matches[0])
    if any(_receipt_semantic(receipt) != semantic for receipt in matches[1:]):
        raise IdempotencyConflictError(
            f"retrieval_id '{normalized_retrieval_id}' has conflicting persisted receipts"
        )
    receipt = matches[0]
    hit = _validated_receipt_hit(receipt, normalized_entry_id)
    return receipt, hit


def append_outcome_event(
    *,
    event_id: str,
    retrieval_id: str,
    entry_id: str,
    application_target: str,
    expected_effect: str,
    actual_result: str,
    recurrence: str,
    user_verdict: str,
    evidence_paths: list[str] | tuple[str, ...],
    base_dir: Path | None = None,
    created_at: str = "",
) -> dict[str, Any]:
    normalized_event_id = _opaque_id(event_id, "event_id")
    normalized_retrieval_id = _opaque_id(retrieval_id, "retrieval_id")
    normalized_entry_id = _required_text(entry_id, "entry_id")
    receipt, hit = load_linked_receipt(
        normalized_retrieval_id,
        normalized_entry_id,
        base_dir=base_dir,
    )

    event = {
        "schema": OUTCOME_EVENT_SCHEMA,
        "event_id": normalized_event_id,
        "retrieval_id": normalized_retrieval_id,
        "entry_id": normalized_entry_id,
        "repo": str(receipt.get("repo") or ""),
        "branch": str(receipt.get("branch") or ""),
        "record_rev": hit["record_rev"],
        "application_target": _required_text(application_target, "application_target"),
        "expected_effect": _required_text(expected_effect, "expected_effect"),
        "actual_result": _required_text(actual_result, "actual_result"),
        "recurrence": _choice(recurrence, "recurrence", RECURRENCE_VALUES),
        "user_verdict": _choice(user_verdict, "user_verdict", USER_VERDICT_VALUES),
        "evidence_paths": _evidence_paths(evidence_paths),
        "created_at": (
            created_at.strip()
            if isinstance(created_at, str) and created_at.strip()
            else now_iso()
        ),
    }
    canonical, _appended = persist_idempotent_jsonl_record(
        outcome_events_path(base_dir),
        event,
        id_field="event_id",
    )
    return canonical


def outcome_feedback_for_entries(
    entry_keys: list[tuple[str, str, str]] | tuple[tuple[str, str, str], ...] | set[tuple[str, str, str]],
    *,
    base_dir: Path | None = None,
) -> dict[tuple[str, str, str], dict[str, Any]]:
    """Summarize verified runtime outcomes without changing durable KB records."""
    requested = {
        (str(repo).strip(), str(branch).strip(), str(entry_id).strip())
        for repo, branch, entry_id in entry_keys
        if str(entry_id).strip()
    }
    if not requested:
        return {}

    summaries: dict[tuple[str, str, str], dict[str, Any]] = {}
    for event in read_jsonl(outcome_events_path(base_dir)):
        entry_id = str(event.get("entry_id") or "").strip()
        key = (
            str(event.get("repo") or "").strip(),
            str(event.get("branch") or "").strip(),
            entry_id,
        )
        if event.get("schema") != OUTCOME_EVENT_SCHEMA or key not in requested:
            continue
        summary = summaries.setdefault(
            key,
            {
                "event_count": 0,
                "accepted_count": 0,
                "rejected_count": 0,
                "mixed_count": 0,
                "not_provided_count": 0,
                "recurrence_observed_count": 0,
                "recurrence_not_observed_count": 0,
                "last_event": {},
            },
        )
        summary["event_count"] += 1
        verdict = str(event.get("user_verdict") or "").strip().lower()
        verdict_key = f"{verdict}_count"
        if verdict_key in summary:
            summary[verdict_key] += 1
        recurrence_value = str(event.get("recurrence") or "").strip().lower()
        if recurrence_value == "observed":
            summary["recurrence_observed_count"] += 1
        elif recurrence_value == "not_observed":
            summary["recurrence_not_observed_count"] += 1
        summary["last_event"] = {
            key: event.get(key)
            for key in (
                "event_id",
                "created_at",
                "record_rev",
                "application_target",
                "expected_effect",
                "actual_result",
                "recurrence",
                "user_verdict",
                "evidence_paths",
            )
        }
    return summaries


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description="Record one outcome linked to a persisted personal-kb retrieval hit."
    )
    parser.add_argument("--event-id", required=True)
    parser.add_argument("--retrieval-id", required=True)
    parser.add_argument("--entry-id", required=True)
    parser.add_argument("--application-target", required=True)
    parser.add_argument("--expected-effect", required=True)
    parser.add_argument("--actual-result", required=True)
    parser.add_argument("--recurrence", required=True)
    parser.add_argument("--user-verdict", required=True)
    parser.add_argument("--evidence-path", action="append", required=True)
    args = parser.parse_args(argv)

    try:
        append_outcome_event(
            event_id=args.event_id,
            retrieval_id=args.retrieval_id,
            entry_id=args.entry_id,
            application_target=args.application_target,
            expected_effect=args.expected_effect,
            actual_result=args.actual_result,
            recurrence=args.recurrence,
            user_verdict=args.user_verdict,
            evidence_paths=args.evidence_path,
        )
    except (ValueError, IdempotencyConflictError, JsonlSafetyError, OSError) as exc:
        sys.stderr.write(f"error: {exc}\n")
        if isinstance(exc, ValueError):
            return 2
        if isinstance(exc, IdempotencyConflictError):
            return 4
        return 5
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
