#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from kb_lib import kb_base_dir, now_iso, read_jsonl


CURRENT_STATUSES = {
    "active",
    "current",
    "implemented",
    "decision_confirmed",
    "partial_current",
}
ACTIVITY_FIELDS = ("updated_ts", "last_used_ts", "ts")


def _status_value(entry: dict[str, Any]) -> str:
    value = entry.get("status")
    if not isinstance(value, str):
        return ""
    return value.strip().lower().replace("-", "_")


def _activity_days(entry: dict[str, Any]) -> list[str]:
    days: list[str] = []
    for key in ACTIVITY_FIELDS:
        value = entry.get(key)
        if isinstance(value, str) and len(value.strip()) >= 10:
            days.append(value.strip()[:10])
    return days


def _archive_decision(entry: dict[str, Any], cutoff: str) -> tuple[bool, str]:
    status = _status_value(entry)
    if not status or status in CURRENT_STATUSES:
        return False, "current_status"

    activity_days = _activity_days(entry)
    if not activity_days:
        return False, "missing_activity_date"
    if any(day >= cutoff for day in activity_days):
        return False, "recent_activity"
    return True, "explicit_noncurrent_and_old"


def _write_jsonl_atomic(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = "".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n" for row in rows)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(data)
        os.replace(tmp_name, path)
    finally:
        tmp = Path(tmp_name)
        if tmp.exists():
            tmp.unlink()


def _archive_path(root: Path, kb_file: Path, cutoff: str) -> Path:
    rel = kb_file.relative_to(root)
    return root.parent / "_archive" / f"pre-{cutoff}" / rel


def archive_old_records(root: Path, cutoff: str, *, apply: bool) -> dict[str, Any]:
    stats: dict[str, Any] = {
        "apply": apply,
        "cutoff": cutoff,
        "root": str(root),
        "archive_root": str(root.parent / "_archive" / f"pre-{cutoff}"),
        "files_seen": 0,
        "files_changed": 0,
        "entries_seen": 0,
        "entries_archived": 0,
        "entries_kept": 0,
        "entries_protected": 0,
        "protected_by_reason": {
            "current_status": 0,
            "recent_activity": 0,
            "missing_activity_date": 0,
        },
        "changed_files": [],
        "ts": now_iso(),
    }

    for kb_file in sorted(root.rglob("kb.jsonl")):
        rows = read_jsonl(kb_file)
        if not rows:
            continue
        stats["files_seen"] += 1
        old_rows: list[dict[str, Any]] = []
        kept_rows: list[dict[str, Any]] = []
        for row in rows:
            stats["entries_seen"] += 1
            should_archive, reason = _archive_decision(row, cutoff)
            if should_archive:
                archived = dict(row)
                archived["_archived_from"] = str(kb_file.relative_to(root))
                archived["_archived_by"] = "kb_archive_old_records.py"
                archived["_archived_reason"] = (
                    f"explicit non-current status and all recorded activity dates before {cutoff}"
                )
                archived["_archived_ts"] = now_iso()
                old_rows.append(archived)
            else:
                kept_rows.append(row)
                stats["entries_protected"] += 1
                stats["protected_by_reason"][reason] += 1

        stats["entries_kept"] += len(kept_rows)

        if not old_rows:
            continue

        stats["files_changed"] += 1
        stats["entries_archived"] += len(old_rows)
        target = _archive_path(root, kb_file, cutoff)
        stats["changed_files"].append({
            "kb": str(kb_file),
            "archive": str(target),
            "archived": len(old_rows),
            "kept": len(kept_rows),
        })
        if apply:
            existing_archive = read_jsonl(target)
            _write_jsonl_atomic(target, [*existing_archive, *old_rows])
            _write_jsonl_atomic(kb_file, kept_rows)

    return stats


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Archive old personal-kb records out of runtime kb.jsonl files. Default is dry-run."
    )
    parser.add_argument("--cutoff", default="2026-07-01", help="Archive entries with date earlier than cutoff YYYY-MM-DD")
    parser.add_argument("--root", default="", help="Override KB repos root; accepts personal-kb or personal-kb/repos")
    parser.add_argument("--apply", action="store_true", help="Actually rewrite kb.jsonl files and archive old entries")
    parser.add_argument("--json", action="store_true", help="Print full JSON report")
    args = parser.parse_args(argv)

    root = Path(args.root).expanduser() if args.root else kb_base_dir()
    if root.name != "repos":
        root = root / "repos"
    if not root.exists():
        print(json.dumps({"ok": False, "error": f"KB repos root not found: {root}"}, ensure_ascii=False))
        return 2

    stats = archive_old_records(root, args.cutoff, apply=args.apply)
    if args.json:
        print(json.dumps(stats, ensure_ascii=False, indent=2))
    else:
        print(
            "KB_ARCHIVE_OLD_RECORDS "
            f"apply={stats['apply']} cutoff={stats['cutoff']} files_changed={stats['files_changed']} "
            f"entries_archived={stats['entries_archived']} entries_kept={stats['entries_kept']} "
            f"entries_protected={stats['entries_protected']}"
        )
        protected = stats["protected_by_reason"]
        print(
            "protected_by_reason "
            f"current_status={protected['current_status']} "
            f"recent_activity={protected['recent_activity']} "
            f"missing_activity_date={protected['missing_activity_date']}"
        )
        print(f"archive_root={stats['archive_root']}")
        for item in stats["changed_files"][:20]:
            print(f"- archived={item['archived']} kept={item['kept']} {item['kb']} -> {item['archive']}")
        if len(stats["changed_files"]) > 20:
            print(f"... {len(stats['changed_files']) - 20} more files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
