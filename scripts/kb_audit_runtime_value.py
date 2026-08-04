#!/usr/bin/env python3
from __future__ import annotations

import argparse
import io
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

if sys.platform == "win32":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)

from kb_lib import kb_base_dir, read_jsonl


def default_closeout_path(base_dir: Path | None = None) -> Path:
    base = base_dir if base_dir is not None else kb_base_dir()
    return base / "_meta" / "closeout.jsonl"


def default_outcomes_path(base_dir: Path | None = None) -> Path:
    base = base_dir if base_dir is not None else kb_base_dir()
    return base / "_meta" / "outcome_events.jsonl"


def _as_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return False


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _parse_ts(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _rate(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round(numerator / denominator, 4)


def build_report(
    *,
    path: Path,
    last_days: int,
    outcomes_path: Path | None = None,
) -> dict[str, Any]:
    rows = read_jsonl(path) if path.exists() else []
    resolved_outcomes_path = outcomes_path if outcomes_path is not None else default_outcomes_path()
    outcome_rows = read_jsonl(resolved_outcomes_path) if resolved_outcomes_path.exists() else []
    since = datetime.now(timezone.utc) - timedelta(days=max(1, last_days))

    recent_rows: list[dict[str, Any]] = []
    skipped_invalid_ts = 0
    for row in rows:
        if row.get("event") != "kb_closeout":
            continue
        ts = _parse_ts(row.get("ts"))
        if ts is None:
            skipped_invalid_ts += 1
            continue
        if ts < since:
            continue
        recent_rows.append(row)

    closeout_total = len(recent_rows)
    hit_closeouts = sum(_as_int(row.get("hit_count")) > 0 for row in recent_rows)
    adopted_closeouts = sum(bool(_as_list(row.get("used_entry_ids"))) for row in recent_rows)
    adopted_hit_closeouts = sum(
        _as_int(row.get("hit_count")) > 0 and bool(_as_list(row.get("used_entry_ids")))
        for row in recent_rows
    )
    no_hit_closeouts = sum(_as_int(row.get("hit_count")) <= 0 for row in recent_rows)
    hit_not_adopted_closeouts = sum(
        _as_int(row.get("hit_count")) > 0 and not bool(_as_list(row.get("used_entry_ids")))
        for row in recent_rows
    )
    written_closeouts = sum(bool(_as_list(row.get("written_entry_ids"))) for row in recent_rows)
    updated_closeouts = sum(bool(_as_list(row.get("updated_entry_ids"))) for row in recent_rows)
    session_brief_hit_closeouts = sum(_as_bool(row.get("session_brief_hit")) for row in recent_rows)
    session_brief_help_closeouts = sum(_as_bool(row.get("session_brief_help")) for row in recent_rows)

    recent_outcomes: list[dict[str, Any]] = []
    skipped_invalid_outcome_ts = 0
    for row in outcome_rows:
        if row.get("schema") != "personal-kb.outcome-event/v1":
            continue
        ts = _parse_ts(row.get("created_at"))
        if ts is None:
            skipped_invalid_outcome_ts += 1
            continue
        if ts >= since:
            recent_outcomes.append(row)

    accepted_outcomes = sum(row.get("user_verdict") == "accepted" for row in recent_outcomes)
    rejected_outcomes = sum(row.get("user_verdict") == "rejected" for row in recent_outcomes)
    decided_outcomes = accepted_outcomes + rejected_outcomes
    recurrence_observed_outcomes = sum(
        row.get("recurrence") == "observed" for row in recent_outcomes
    )
    recurrence_not_observed_outcomes = sum(
        row.get("recurrence") == "not_observed" for row in recent_outcomes
    )
    recurrence_decided_outcomes = recurrence_observed_outcomes + recurrence_not_observed_outcomes

    summary = (
        f"{last_days}d closeouts={closeout_total}, hits={hit_closeouts}, adopted={adopted_hit_closeouts}, "
        f"use_rate={_rate(adopted_hit_closeouts, hit_closeouts)}, "
        f"outcomes={len(recent_outcomes)}, acceptance={_rate(accepted_outcomes, decided_outcomes)}"
    )

    return {
        "closeout_path": str(path),
        "outcomes_path": str(resolved_outcomes_path),
        "last_days": max(1, last_days),
        "since": since.isoformat(timespec="seconds"),
        "closeout_total": closeout_total,
        "hit_closeouts": hit_closeouts,
        "adopted_closeouts": adopted_closeouts,
        "adopted_hit_closeouts": adopted_hit_closeouts,
        "use_rate": _rate(adopted_hit_closeouts, hit_closeouts),
        "no_hit_closeouts": no_hit_closeouts,
        "hit_not_adopted_closeouts": hit_not_adopted_closeouts,
        "written_closeouts": written_closeouts,
        "updated_closeouts": updated_closeouts,
        "session_brief_hit_closeouts": session_brief_hit_closeouts,
        "session_brief_help_closeouts": session_brief_help_closeouts,
        "session_brief_help_rate": _rate(session_brief_help_closeouts, session_brief_hit_closeouts),
        "outcome_event_total": len(recent_outcomes),
        "accepted_outcomes": accepted_outcomes,
        "rejected_outcomes": rejected_outcomes,
        "user_acceptance_rate": _rate(accepted_outcomes, decided_outcomes),
        "recurrence_observed_outcomes": recurrence_observed_outcomes,
        "recurrence_not_observed_outcomes": recurrence_not_observed_outcomes,
        "recurrence_rate": _rate(recurrence_observed_outcomes, recurrence_decided_outcomes),
        "skipped_invalid_ts_rows": skipped_invalid_ts,
        "skipped_invalid_outcome_ts_rows": skipped_invalid_outcome_ts,
        "summary": summary,
    }


def _print_text(report: dict[str, Any]) -> None:
    print(f"KB_RUNTIME_VALUE {report['summary']}")
    print(json.dumps(report, ensure_ascii=False, indent=2))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Audit whether recent personal-kb closeouts are producing usable runtime value.")
    parser.add_argument("--closeout", default="", help="Override closeout.jsonl path")
    parser.add_argument("--outcomes", default="", help="Override outcome_events.jsonl path")
    parser.add_argument("--last-days", type=int, default=7, help="Only include recent closeouts from the last N days")
    parser.add_argument("--text", action="store_true", help="Print a short text line before the JSON payload")
    args = parser.parse_args(argv)

    path = Path(args.closeout).expanduser() if args.closeout else default_closeout_path()
    outcomes_path = Path(args.outcomes).expanduser() if args.outcomes else default_outcomes_path()
    report = build_report(path=path, outcomes_path=outcomes_path, last_days=args.last_days)
    if args.text:
        _print_text(report)
    else:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
