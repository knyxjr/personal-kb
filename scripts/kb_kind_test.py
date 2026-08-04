#!/usr/bin/env python3
from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from unittest.mock import patch

_ORIGINAL_PLATFORM = sys.platform
sys.platform = "test"
try:
    import kb_add
    import kb_search
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


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def call_main(func: Callable[[list[str]], int], argv: list[str]) -> int:
    try:
        return func(argv)
    except SystemExit as exc:
        code = exc.code
        return code if isinstance(code, int) else 1


def test_add_writes_kind_field() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        ctx = make_context(root)

        stdout = io.StringIO()
        with (
            patch.object(kb_add, "resolve_context", return_value=ctx),
            patch.object(kb_add, "search_related_entries", return_value=[]),
            patch.object(kb_add.Path, "home", return_value=root / "home"),
            contextlib.redirect_stdout(stdout),
        ):
            rc = call_main(
                kb_add.main,
                [
                    "--kind",
                    "issue",
                    "--title",
                    "login timeout",
                    "--story",
                    "login fails with timeout",
                ],
            )

        assert rc == 0
        rows = read_jsonl(ctx.kb_path)
        assert rows[0]["kind"] == "issue"
        assert "type" not in rows[0]
        summary = json.loads(stdout.getvalue())
        assert summary["kind"] == "issue"
        assert "type" not in summary


def test_add_rejects_unknown_kind() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        ctx = make_context(root)

        stderr = io.StringIO()
        with (
            patch.object(kb_add, "resolve_context", return_value=ctx),
            patch.object(kb_add.Path, "home", return_value=root / "home"),
            contextlib.redirect_stderr(stderr),
        ):
            rc = call_main(
                kb_add.main,
                [
                    "--kind",
                    "bugfix",
                    "--title",
                    "legacy type should fail",
                    "--story",
                    "old type is not a valid kind",
                ],
            )

        assert rc == 2
        assert "invalid choice" in stderr.getvalue()
        assert not ctx.kb_path.exists()


def test_add_legacy_type_maps_to_kind_without_writing_type() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        ctx = make_context(root)

        stdout = io.StringIO()
        stderr = io.StringIO()
        with (
            patch.object(kb_add, "resolve_context", return_value=ctx),
            patch.object(kb_add, "search_related_entries", return_value=[]),
            patch.object(kb_add.Path, "home", return_value=root / "home"),
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
        ):
            rc = call_main(
                kb_add.main,
                [
                    "--type",
                    "bugfix",
                    "--title",
                    "legacy bugfix",
                    "--story",
                    "old runbooks still use --type bugfix",
                ],
            )

        assert rc == 0
        assert "--type 已废弃" in stderr.getvalue()
        rows = read_jsonl(ctx.kb_path)
        assert rows[0]["kind"] == "issue"
        assert "type" not in rows[0]
        summary = json.loads(stdout.getvalue())
        assert summary["kind"] == "issue"


def test_search_filters_by_kind() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        ctx = make_context(root)
        write_jsonl(
            ctx.kb_path,
            [
                {
                    "id": "issue-1",
                    "ts": "2026-01-02T00:00:00+00:00",
                    "kind": "issue",
                    "repo": "demo",
                    "branch": "main",
                    "title": "needle login timeout",
                    "story": "same keyword",
                    "tags": ["needle"],
                },
                {
                    "id": "map-1",
                    "ts": "2026-01-03T00:00:00+00:00",
                    "kind": "map",
                    "repo": "demo",
                    "branch": "main",
                    "title": "needle service map",
                    "story": "same keyword",
                    "tags": ["needle"],
                },
            ],
        )

        stdout = io.StringIO()
        with (
            patch.object(kb_search, "resolve_context", return_value=ctx),
            patch.object(kb_search, "global_bucket_dir", return_value=root / "global"),
            contextlib.redirect_stdout(stdout),
        ):
            rc = call_main(kb_search.main, ["needle", "--kind", "issue", "--json"])

        assert rc == 0
        rows = json.loads(stdout.getvalue())
        assert [row["id"] for row in rows] == ["issue-1"]
        assert rows[0]["kind"] == "issue"


def test_search_legacy_type_filter_maps_to_kind() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        ctx = make_context(root)
        write_jsonl(
            ctx.kb_path,
            [
                {
                    "id": "issue-1",
                    "ts": "2026-01-02T00:00:00+00:00",
                    "kind": "issue",
                    "repo": "demo",
                    "branch": "main",
                    "title": "needle login timeout",
                    "story": "same keyword",
                    "tags": ["needle"],
                },
                {
                    "id": "map-1",
                    "ts": "2026-01-03T00:00:00+00:00",
                    "kind": "map",
                    "repo": "demo",
                    "branch": "main",
                    "title": "needle service map",
                    "story": "same keyword",
                    "tags": ["needle"],
                },
            ],
        )

        stdout = io.StringIO()
        stderr = io.StringIO()
        with (
            patch.object(kb_search, "resolve_context", return_value=ctx),
            patch.object(kb_search, "global_bucket_dir", return_value=root / "global"),
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
        ):
            rc = call_main(kb_search.main, ["needle", "--type", "bugfix", "--json"])

        assert rc == 0
        assert "--type 已废弃" in stderr.getvalue()
        rows = json.loads(stdout.getvalue())
        assert [row["id"] for row in rows] == ["issue-1"]
        assert rows[0]["kind"] == "issue"


def main() -> int:
    tests = [
        test_add_writes_kind_field,
        test_add_rejects_unknown_kind,
        test_add_legacy_type_maps_to_kind_without_writing_type,
        test_search_filters_by_kind,
        test_search_legacy_type_filter_maps_to_kind,
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

    print("kb_kind tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
