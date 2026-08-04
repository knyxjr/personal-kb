from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path


PROJECT_DOC_EXTENSIONS = {
    ".md",
    ".markdown",
    ".txt",
    ".rst",
    ".adoc",
    ".asciidoc",
    ".doc",
    ".docx",
    ".pdf",
}

TEXT_EVIDENCE_EXTENSIONS = {
    ".log",
    ".csv",
    ".json",
    ".yml",
    ".yaml",
    ".xml",
    ".properties",
    ".sql",
}

BINARY_EXTENSIONS = {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".bmp",
    ".webp",
    ".ico",
    ".zip",
    ".rar",
    ".7z",
    ".gz",
    ".tar",
    ".jar",
    ".war",
    ".class",
    ".exe",
    ".dll",
}

ARTIFACT_DIR_NAMES = {
    "artifacts",
    "artifact",
    "assets",
    "tmp",
    "temp",
    "target",
    "build",
    "dist",
    "node_modules",
    ".git",
    ".idea",
    "__pycache__",
}

PROJECT_DOC_HINTS = {
    "readme",
    "notes",
    "decision",
    "decisions",
    "design",
    "方案",
    "计划",
    "记录",
    "复盘",
    "需求",
    "验收",
    "trace",
    "topic",
    "links",
    "log",
}


@dataclass
class ManifestItem:
    path: str
    relative_path: str
    size: int
    sha256_12: str
    kind: str
    include_default: bool
    reason: str


def _hash_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()[:12]


def _has_artifact_dir(path: Path, root: Path) -> bool:
    try:
        relative = path.relative_to(root)
    except ValueError:
        return False
    parts = {part.lower() for part in relative.parts[:-1]}
    return bool(parts & ARTIFACT_DIR_NAMES)


def _name_has_project_hint(path: Path) -> bool:
    raw = " ".join(part.lower() for part in path.parts)
    return any(hint.lower() in raw for hint in PROJECT_DOC_HINTS)


def _classify(path: Path, root: Path, artifact_max_bytes: int) -> tuple[str, bool, str]:
    name = path.name
    suffix = path.suffix.lower()

    if name == ".gitkeep":
        return "ignored_marker", False, ".gitkeep marker"

    if suffix in BINARY_EXTENSIONS:
        return "binary_or_asset", False, f"binary/asset extension {suffix}"

    in_artifact_dir = _has_artifact_dir(path, root)
    size = path.stat().st_size

    if suffix in PROJECT_DOC_EXTENSIONS:
        if in_artifact_dir and suffix not in {".md", ".markdown", ".txt", ".rst", ".adoc", ".asciidoc"}:
            return "artifact_document", False, "document under artifact-like directory"
        return "project_document", True, f"project document extension {suffix}"

    if suffix in TEXT_EVIDENCE_EXTENSIONS:
        if in_artifact_dir and size > artifact_max_bytes:
            return "artifact_evidence", False, "large text evidence under artifact-like directory"
        if suffix == ".json" and size > artifact_max_bytes:
            return "large_json_artifact", False, "large json evidence"
        if _name_has_project_hint(path):
            return "text_project_evidence", True, f"text evidence with project hint {suffix}"
        return "text_evidence", False, f"text evidence extension {suffix}"

    if in_artifact_dir:
        return "artifact_unknown", False, "unknown file under artifact-like directory"

    return "unknown", False, "unknown extension"


def build_manifest(root: Path, artifact_max_bytes: int) -> list[ManifestItem]:
    root = root.resolve()
    items: list[ManifestItem] = []
    for path in sorted((p for p in root.rglob("*") if p.is_file()), key=lambda p: str(p).lower()):
        kind, include_default, reason = _classify(path, root, artifact_max_bytes)
        try:
            sha = _hash_file(path)
        except OSError:
            sha = ""
        stat = path.stat()
        relative_path = str(path.relative_to(root))
        items.append(
            ManifestItem(
                path=str(path),
                relative_path=relative_path,
                size=stat.st_size,
                sha256_12=sha,
                kind=kind,
                include_default=include_default,
                reason=reason,
            )
        )
    return items


def _write_json(items: list[ManifestItem]) -> None:
    json.dump([asdict(item) for item in items], sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")


def _write_jsonl(items: list[ManifestItem]) -> None:
    for item in items:
        sys.stdout.write(json.dumps(asdict(item), ensure_ascii=False, separators=(",", ":")) + "\n")


def _write_csv(items: list[ManifestItem]) -> None:
    writer = csv.DictWriter(
        sys.stdout,
        fieldnames=["relative_path", "size", "sha256_12", "kind", "include_default", "reason", "path"],
        lineterminator="\n",
    )
    writer.writeheader()
    for item in items:
        writer.writerow(asdict(item))


def _write_table(items: list[ManifestItem]) -> None:
    rows = [
        [
            item.relative_path,
            str(item.size),
            item.kind,
            "yes" if item.include_default else "no",
            item.reason,
        ]
        for item in items
    ]
    headers = ["relative_path", "size", "kind", "include", "reason"]
    widths = [len(header) for header in headers]
    for row in rows:
        for index, value in enumerate(row):
            widths[index] = min(max(widths[index], len(value)), 80)

    def format_row(values: list[str]) -> str:
        cells: list[str] = []
        for index, value in enumerate(values):
            width = widths[index]
            display = value if len(value) <= width else value[: width - 1] + "…"
            cells.append(display.ljust(width))
        return "  ".join(cells)

    sys.stdout.write(format_row(headers) + "\n")
    sys.stdout.write(format_row(["-" * width for width in widths]) + "\n")
    for row in rows:
        sys.stdout.write(format_row(row) + "\n")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Build a read-only manifest for docs/project documents.")
    parser.add_argument("root", nargs="?", default="docs", help="Docs directory to scan (default: docs)")
    parser.add_argument(
        "--format",
        choices=["table", "json", "jsonl", "csv"],
        default="jsonl",
        help="Output format (default: jsonl)",
    )
    parser.add_argument(
        "--include",
        choices=["all", "default"],
        default="all",
        help="Output all files or only default-included project documents (default: all)",
    )
    parser.add_argument(
        "--artifact-max-kb",
        type=int,
        default=256,
        help="Text evidence larger than this under artifact-like dirs is excluded by default (default: 256)",
    )
    args = parser.parse_args(argv)

    root = Path(args.root)
    if not root.exists() or not root.is_dir():
        sys.stderr.write(f"Not a directory: {root}\n")
        return 2

    items = build_manifest(root, artifact_max_bytes=args.artifact_max_kb * 1024)
    if args.include == "default":
        items = [item for item in items if item.include_default]

    if args.format == "json":
        _write_json(items)
    elif args.format == "jsonl":
        _write_jsonl(items)
    elif args.format == "csv":
        _write_csv(items)
    else:
        _write_table(items)

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
