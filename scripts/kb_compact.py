from __future__ import annotations

import argparse
import gzip
import json
import shutil
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from kb_lib import append_jsonl, ensure_branch_layout, now_iso, resolve_context, write_index


def _parse_iso(ts: str) -> float | None:
    try:
        return datetime.fromisoformat(ts).timestamp()
    except ValueError:
        return None


def _next_part_path(month_dir: Path) -> Path:
    month_dir.mkdir(parents=True, exist_ok=True)
    existing = sorted(month_dir.glob("part-*.jsonl.gz"))
    max_n = 0
    for p in existing:
        name = p.name
        if not name.startswith("part-") or not name.endswith(".jsonl.gz"):
            continue
        mid = name[len("part-") : -len(".jsonl.gz")]
        try:
            n = int(mid)
        except ValueError:
            continue
        max_n = max(max_n, n)
    return month_dir / f"part-{max_n + 1:04d}.jsonl.gz"


def _load_records(lines: list[str]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for raw in lines:
        s = raw.strip()
        if not s:
            continue
        try:
            obj = json.loads(s)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            records.append(obj)
    return records


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Compact kb.jsonl when it grows too large.")
    parser.add_argument("--max-mb", type=float, default=20.0, help="Compact when kb.jsonl > max-mb (default: 20)")
    parser.add_argument(
        "--keep-ratio",
        type=float,
        default=0.30,
        help="Keep newest ratio by lines, archive the rest (default: 0.30)",
    )
    parser.add_argument("--repo", default="", help="Override repo bucket (optional)")
    parser.add_argument("--branch", default="", help="Override branch bucket (optional)")
    parser.add_argument("--dry-run", action="store_true", help="Show plan but do not write anything")
    parser.add_argument("--force", action="store_true", help="Force compact even if below threshold")
    args = parser.parse_args(argv)

    if args.keep_ratio <= 0.0 or args.keep_ratio >= 1.0:
        sys.stderr.write("--keep-ratio must be between 0 and 1\n")
        return 2

    ctx = resolve_context(
        cwd=Path.cwd(),
        repo_name_override=(args.repo.strip() or None),
        branch_override=(args.branch.strip() or None),
    )
    ensure_branch_layout(ctx)

    if not ctx.kb_path.exists():
        sys.stdout.write("kb.jsonl not found, nothing to compact.\n")
        return 0

    max_bytes = int(args.max_mb * 1024 * 1024)
    size_bytes = ctx.kb_path.stat().st_size
    if not args.force and size_bytes <= max_bytes:
        sys.stdout.write(f"kb.jsonl size={size_bytes} bytes <= {max_bytes} bytes, skip.\n")
        return 0

    with ctx.kb_path.open("r", encoding="utf-8") as f:
        lines = f.read().splitlines()

    if len(lines) < 2:
        sys.stdout.write("Not enough entries to compact.\n")
        return 0

    archive_count = int(len(lines) * (1.0 - args.keep_ratio))
    archive_count = max(1, min(archive_count, len(lines) - 1))
    archived_lines = lines[:archive_count]
    kept_lines = lines[archive_count:]

    # Archive target path (by current month).
    now = datetime.now().astimezone()
    month_dir = ctx.archive_dir / now.strftime("%Y-%m")
    archive_path = _next_part_path(month_dir)

    if args.dry_run:
        sys.stdout.write(
            f"Would archive {archive_count}/{len(lines)} lines to {archive_path} and keep {len(kept_lines)} lines.\n"
        )
        return 0

    # Backup kb.jsonl before rewriting.
    backup_path = ctx.kb_path.with_name(f"kb.jsonl.bak-{now.strftime('%Y%m%d-%H%M%S')}")
    shutil.copy2(ctx.kb_path, backup_path)

    # Write gzip archive.
    archive_blob = "\n".join(archived_lines) + "\n"
    with gzip.open(archive_path, "wt", encoding="utf-8", newline="\n") as gf:
        gf.write(archive_blob)

    # Heuristic summary record.
    records = _load_records(archived_lines)
    tag_counter: Counter[str] = Counter()
    kind_counter: Counter[str] = Counter()
    ts_values: list[float] = []
    titles: list[str] = []
    for r in records:
        kind = r.get("kind")
        if isinstance(kind, str) and kind:
            kind_counter[kind] += 1
        tags = r.get("tags")
        if isinstance(tags, list):
            for t in tags:
                if isinstance(t, str) and t:
                    tag_counter[t] += 1
        ts = r.get("ts")
        if isinstance(ts, str):
            v = _parse_iso(ts)
            if v is not None:
                ts_values.append(v)
        title = r.get("title")
        if isinstance(title, str) and title:
            titles.append(title)

    ts_from = None
    ts_to = None
    if ts_values:
        ts_from = datetime.fromtimestamp(min(ts_values)).astimezone().isoformat(timespec="seconds")
        ts_to = datetime.fromtimestamp(max(ts_values)).astimezone().isoformat(timespec="seconds")

    summary_entry: dict[str, Any] = {
        "ts": now_iso(),
        "kind": "implementation",
        "repo": ctx.repo_name,
        "branch": ctx.branch,
        "branch_dir": ctx.branch_dir,
        "archived": {
            "file": str(archive_path.relative_to(ctx.branch_path)).replace("\\", "/"),
            "count": archive_count,
            "range": {"from": ts_from, "to": ts_to},
        },
        "top_tags": [t for t, _ in tag_counter.most_common(10)],
        "top_kinds": [kind for kind, _ in kind_counter.most_common(10)],
        "sample_titles": titles[:20],
    }
    append_jsonl(ctx.summary_path, summary_entry)

    # Rewrite kb.jsonl with kept lines.
    new_blob = "\n".join(kept_lines) + "\n"
    with ctx.kb_path.open("w", encoding="utf-8", newline="\n") as f:
        f.write(new_blob)

    write_index(ctx)

    sys.stdout.write(f"Archived {archive_count} entries to {archive_path}\n")
    sys.stdout.write(f"Backup: {backup_path}\n")
    sys.stdout.write(f"Kept entries: {len(kept_lines)}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
