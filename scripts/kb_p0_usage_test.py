#!/usr/bin/env python3
from __future__ import annotations

import contextlib
import base64
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
    import kb_archive_old_records
    import kb_closeout
    import kb_lib
    import kb_search
    import kb_session_brief
    import kb_update
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
    routing_source: str = "test"
    candidate_repos: tuple[str, ...] = ()


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


def test_search_is_readonly_by_default() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        ctx = make_context(root)
        write_jsonl(
            ctx.kb_path,
            [
                {
                    "id": "entry-1",
                    "ts": "2026-01-01T00:00:00+00:00",
                    "kind": "experience",
                    "repo": "demo",
                    "branch": "main",
                    "title": "needle entry",
                    "story": "needle body",
                    "tags": ["needle"],
                    "call_count": 3,
                    "used_count": 0,
                    "last_used_ts": "",
                }
            ],
        )

        with (
            patch.object(kb_search, "resolve_context", return_value=ctx),
            patch.object(kb_search, "global_bucket_dir", return_value=root / "global"),
            patch.object(kb_search.Path, "home", return_value=root / "home"),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            rc = call_main(kb_search.main, ["needle", "--json"])

        assert rc == 0
        rows = read_jsonl(ctx.kb_path)
        assert rows[0]["call_count"] == 3
        assert rows[0]["used_count"] == 0
        assert rows[0]["last_used_ts"] == ""
        assert not (root / "home" / ".codex" / "personal-kb-logs" / "kb_search.log").exists()


def test_config_cannot_enable_search_logging_without_flag() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        ctx = make_context(root)
        write_jsonl(
            ctx.kb_path,
            [
                {
                    "id": "entry-1",
                    "ts": "2026-01-01T00:00:00+00:00",
                    "kind": "experience",
                    "repo": "demo",
                    "branch": "main",
                    "title": "needle entry",
                    "story": "needle body",
                    "tags": ["needle"],
                }
            ],
        )

        with (
            patch.object(kb_search, "resolve_context", return_value=ctx),
            patch.object(kb_search, "global_bucket_dir", return_value=root / "global"),
            patch.object(kb_search.Path, "home", return_value=root / "home"),
            patch.object(kb_search, "load_config", return_value={"search": {"log_search": True}}),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            rc = call_main(kb_search.main, ["needle", "--json"])

        assert rc == 0
        assert not (root / "home" / ".codex" / "personal-kb-logs" / "kb_search.log").exists()


def test_search_does_not_inject_aggregation_view_by_default() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        ctx = make_context(root)
        write_jsonl(
            ctx.kb_path,
            [
                {
                    "id": "entry-1",
                    "ts": "2026-01-01T00:00:00+00:00",
                    "kind": "experience",
                    "repo": "demo",
                    "branch": "main",
                    "title": "needle entry",
                    "story": "needle body",
                    "tags": ["needle"],
                }
            ],
        )

        def fail_if_called(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
            raise AssertionError("aggregation view should be opt-in")

        stdout = io.StringIO()
        with (
            patch.object(kb_search, "resolve_context", return_value=ctx),
            patch.object(kb_search, "global_bucket_dir", return_value=root / "global"),
            patch.object(kb_search, "AGGREGATION_ENHANCER_AVAILABLE", True),
            patch.object(kb_search, "inject_aggregation_view", fail_if_called),
            contextlib.redirect_stdout(stdout),
        ):
            rc = call_main(kb_search.main, ["needle", "--json"])

        assert rc == 0
        rows = json.loads(stdout.getvalue())
        assert [row["id"] for row in rows] == ["entry-1"]


def test_relevance_score_ignores_call_count() -> None:
    base = {"title": "needle entry", "tags": ["needle"], "story": "needle body"}
    cold = {**base, "call_count": 0}
    hot = {**base, "call_count": 100}

    assert kb_search._relevance_score(cold, "needle") == kb_search._relevance_score(hot, "needle")


def test_update_use_records_runtime_heat_without_rewriting_durable_record() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        ctx = make_context(root)
        write_jsonl(
            ctx.kb_path,
            [
                {
                    "id": "entry-1",
                    "ts": "2026-01-01T00:00:00+00:00",
                    "kind": "experience",
                    "repo": "demo",
                    "branch": "main",
                    "title": "needle entry",
                    "story": "body",
                    "tags": [],
                    "used_count": 1,
                    "last_used_ts": "",
                }
            ],
        )

        stdout = io.StringIO()
        with (
            patch.object(kb_update, "resolve_context", return_value=ctx),
            contextlib.redirect_stdout(stdout),
        ):
            rc = call_main(kb_update.main, ["use", "entry-1"])

        assert rc == 0
        rows = read_jsonl(ctx.kb_path)
        assert rows[0]["used_count"] == 1
        assert rows[0]["last_used_ts"] == ""
        summary = json.loads(stdout.getvalue())
        assert summary["status"] == "ok"
        assert summary["action"] == "use"
        assert summary["id"] == "entry-1"
        assert summary["legacy_used_count"] == 1
        assert summary["runtime_heated_count"] == 1
        assert summary["effective_used_count"] == 2
        events = read_jsonl(root / "repos" / "_meta" / "adoption_events.jsonl")
        assert events[0]["entry_id"] == "entry-1"
        assert events[0]["effect"] == "legacy"
        assert not (root / "global" / "kb.jsonl").exists()


def test_resolve_context_explicit_repo_and_branch_bypass_child_discovery() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        workspace = root / "workspace"
        workspace.mkdir()
        stderr = io.StringIO()
        with (
            patch.object(kb_lib, "_repo_root", side_effect=AssertionError("explicit repo and branch must bypass git discovery")),
            patch.object(kb_lib, "_find_all_child_git_repos", side_effect=AssertionError("explicit repo must bypass child discovery")),
            patch.object(kb_lib, "kb_base_dir", return_value=root / "repos"),
            patch.object(kb_lib, "load_config", return_value={}),
            contextlib.redirect_stderr(stderr),
        ):
            ctx = kb_lib.resolve_context(
                cwd=workspace,
                repo_name_override="demo/service",
                branch_override="feature/x",
            )

        assert ctx.repo_name == "demo/service"
        assert ctx.branch == "feature/x"
        assert ctx.kb_path == root / "repos" / "demo" / "service" / "feature__x" / "kb.jsonl"
        assert ctx.routing_source == "explicit_override"
        assert ctx.candidate_repos == ()
        assert stderr.getvalue() == ""


def test_resolve_context_repo_override_keeps_current_git_branch() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        workspace = root / "workspace"
        workspace.mkdir()
        with (
            patch.object(kb_lib, "_repo_root", return_value=workspace),
            patch.object(kb_lib, "_current_branch", return_value="main"),
            patch.object(kb_lib, "_find_all_child_git_repos", side_effect=AssertionError("current repo must bypass child discovery")),
            patch.object(kb_lib, "kb_base_dir", return_value=root / "repos"),
            patch.object(kb_lib, "load_config", return_value={}),
        ):
            ctx = kb_lib.resolve_context(cwd=workspace, repo_name_override="chosen")

        assert ctx.repo_name == "chosen"
        assert ctx.branch == "main"
        assert ctx.routing_source == "explicit_override"


def test_resolve_context_ambiguous_multi_repo_falls_back_silently() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        workspace = root / "workspace"
        workspace.mkdir()
        child_repos = [workspace / "repo-a", workspace / "repo-b"]
        stderr = io.StringIO()
        with (
            patch.object(kb_lib, "_repo_root", return_value=None),
            patch.object(kb_lib, "_find_all_child_git_repos", return_value=child_repos),
            patch.object(kb_lib, "_infer_repo_from_task_hint", return_value=None),
            patch.object(kb_lib, "kb_base_dir", return_value=root / "repos"),
            patch.object(kb_lib, "load_config", return_value={}),
            contextlib.redirect_stderr(stderr),
        ):
            ctx = kb_lib.resolve_context(cwd=workspace, task_hint="ambiguous task", operation="closeout")

        assert ctx.repo_name == "workspace"
        assert ctx.branch == "no-git"
        assert ctx.kb_path == root / "repos" / "workspace" / "no-git" / "kb.jsonl"
        assert ctx.routing_source == "workspace_fallback"
        assert ctx.candidate_repos == ("repo-a", "repo-b")
        assert stderr.getvalue() == ""


def test_closeout_writes_ai_only_audit_event() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        ctx = make_context(root)
        write_jsonl(
            ctx.kb_path,
            [
                {
                    "id": "entry-1",
                    "ts": "2026-01-01T00:00:00+00:00",
                    "kind": "experience",
                    "repo": "demo",
                    "branch": "main",
                    "title": "usable context",
                    "story": "body",
                    "tags": [],
                    "used_count": 0,
                    "last_used_ts": "",
                }
            ],
        )

        stdout = io.StringIO()
        with (
            patch.object(kb_closeout, "resolve_context", return_value=ctx),
            patch.object(kb_closeout.kb_update, "resolve_context", return_value=ctx),
            patch.object(kb_closeout, "kb_base_dir", return_value=root),
            contextlib.redirect_stdout(stdout),
        ):
            rc = call_main(
                kb_closeout.main,
                [
                    "--query",
                    "personal-kb test",
                    "--hit-count",
                    "3",
                    "--rag-calls",
                    "1",
                    "--allowed-hit-id",
                    "entry-1",
                    "--used",
                    "entry-1",
                    "--updated",
                    "entry-2",
                    "--reason",
                    "stable conclusion updated",
                    "--verbose",
                ],
            )

        assert rc == 0
        summary = json.loads(stdout.getvalue())
        assert summary["status"] == "ok"
        assert summary["heated"] == 1
        assert summary["heat_failed"] == 0
        assert set(summary) == {"status", "path", "event", "heated", "heat_failed", "session_briefs_written"}
        kb_rows = read_jsonl(ctx.kb_path)
        assert kb_rows[0]["used_count"] == 0
        assert kb_rows[0]["last_used_ts"] == ""
        adoption_rows = read_jsonl(root / "repos" / "_meta" / "adoption_events.jsonl")
        assert adoption_rows[0]["entry_id"] == "entry-1"
        path = Path(summary["path"])
        rows = read_jsonl(path)
        assert len(rows) == 1
        event = rows[0]
        assert event["event"] == "kb_closeout"
        assert event["mode"] == "ai_only_runtime_audit"
        assert event["queries"] == ["personal-kb test"]
        assert event["hit_count"] == 3
        assert event["rag_calls"] == 1
        assert event["used_entry_ids"] == ["entry-1"]
        assert event["hit_entry_ids"] == ["entry-1"]
        assert event["updated_entry_ids"] == ["entry-2"]
        assert event["skipped_reason"] == "stable conclusion updated"
        assert event["heat_applied"] is True
        assert event["heated_entry_ids"] == ["entry-1"]
        assert event["heat_failed_entry_ids"] == []


def test_closeout_records_locator_help_without_heating() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        ctx = make_context(root)
        write_jsonl(
            ctx.kb_path,
            [{
                "id": "locator-1",
                "ts": "2026-07-01T00:00:00+00:00",
                "kind": "map",
                "repo": "demo",
                "branch": "main",
                "title": "artifact locator",
                "story": "points to current evidence",
                "used_count": 0,
                "last_used_ts": "",
            }],
        )
        stdout = io.StringIO()
        with (
            patch.object(kb_closeout, "resolve_context", return_value=ctx),
            patch.object(kb_closeout.kb_update, "resolve_context", return_value=ctx),
            patch.object(kb_closeout, "kb_base_dir", return_value=root),
            contextlib.redirect_stdout(stdout),
        ):
            rc = call_main(kb_closeout.main, [
                "--query", "find artifact", "--hit-count", "1",
                "--allowed-hit-id", "locator-1", "--used-locate", "locator-1",
                "--linked-retrieval-id", "test-link-1",
                "--verbose",
            ])
        assert rc == 0
        summary = json.loads(stdout.getvalue())
        assert summary["heated"] == 0
        event = read_jsonl(Path(summary["path"]))[0]
        assert event["used_entry_ids"] == ["locator-1"]
        assert event["adoption_effects"]["locate"] == ["locator-1"]
        assert event["heat_entry_ids"] == []
        assert event["linked_retrieval_ids"] == ["test-link-1"]
        assert read_jsonl(ctx.kb_path)[0]["used_count"] == 0


def test_closeout_rejects_invalid_linked_retrieval_id_before_writes() -> None:
    stderr = io.StringIO()
    with contextlib.redirect_stderr(stderr):
        rc = call_main(
            kb_closeout.main,
            [
                "--query",
                "demo",
                "--rag-calls",
                "1",
                "--hit-count",
                "0",
                "--reason",
                "no usable hit",
                "--linked-retrieval-id",
                "contains whitespace",
            ],
        )
    assert rc == 2
    assert "linked retrieval ids must be" in stderr.getvalue()


def test_closeout_normalizes_duplicate_effects_to_highest_level() -> None:
    args = type("Args", (), {
        "json_file": "", "json": "", "repo": "", "branch": "", "query": ["q"],
        "used": [], "used_locate": ["entry-1"], "used_decide": ["entry-1"],
        "used_fix": [], "used_write": [], "written": [], "updated": [],
        "allowed_hit_id": ["entry-1"], "hit_count": 1, "rag_calls": 1, "reason": "",
        "session_brief_hit": False, "session_brief_help": False,
    })()
    with (
        patch.object(kb_closeout, "resolve_context", return_value=make_context(Path("/tmp/demo"))),
        patch.object(kb_closeout, "kb_base_dir", return_value=Path("/tmp/no-session-briefs")),
    ):
        event = kb_closeout.build_closeout(args)
    assert event["adoption_effects"]["locate"] == []
    assert event["adoption_effects"]["decide"] == ["entry-1"]
    assert event["heat_entry_ids"] == ["entry-1"]


def test_closeout_can_write_recent_session_brief() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        ctx = make_context(root)
        write_jsonl(
            ctx.kb_path,
            [
                {
                    "id": "entry-1",
                    "ts": "2026-01-01T00:00:00+00:00",
                    "kind": "implementation",
                    "repo": "demo",
                    "branch": "main",
                    "title": "usable context",
                    "story": "body",
                    "tags": [],
                    "used_count": 0,
                    "last_used_ts": "",
                }
            ],
        )

        stdout = io.StringIO()
        with (
            patch.object(kb_closeout, "resolve_context", return_value=ctx),
            patch.object(kb_closeout.kb_update, "resolve_context", return_value=ctx),
            patch.object(kb_closeout, "kb_base_dir", return_value=root),
            patch.object(kb_session_brief, "resolve_context", return_value=ctx),
            contextlib.redirect_stdout(stdout),
        ):
            rc = call_main(
                kb_closeout.main,
                [
                    "--query",
                    "personal-kb closeout recent brief",
                    "--hit-count",
                    "2",
                    "--rag-calls",
                    "1",
                    "--used",
                    "entry-1",
                    "--reason",
                    "fixed closeout and brief flow",
                    "--auto-session-brief",
                    "--session-brief-summary",
                    "Closeout now records an explicit short-lived workflow summary.",
                    "--session-brief-hit",
                    "--session-brief-help",
                    "--session-brief-source",
                    "codex",
                    "--session-id",
                    "session-1",
                    "--verbose",
                ],
            )

        assert rc == 0
        summary = json.loads(stdout.getvalue())
        assert summary["session_briefs_written"] == 1
        brief_rows = read_jsonl(root / "_meta" / "session_briefs.jsonl")
        assert len(brief_rows) == 1
        brief = brief_rows[0]
        assert brief["event"] == "kb_session_brief"
        assert brief["kind"] == "session_brief"
        assert brief["repo"] == "demo"
        assert brief["branch"] == "main"
        assert brief["source"] == "codex"
        assert brief["session_id"] == "session-1"
        assert brief["used_entry_ids"] == ["entry-1"]
        assert "recent-session" in brief["tags"]
        closeout_rows = read_jsonl(root / "_meta" / "closeout.jsonl")
        assert closeout_rows[0]["session_brief_hit"] is True
        assert closeout_rows[0]["session_brief_help"] is True


def test_closeout_infers_rag_calls_from_hit_count_when_omitted() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        ctx = make_context(root)
        stdout = io.StringIO()
        with (
            patch.object(kb_closeout, "resolve_context", return_value=ctx),
            patch.object(kb_closeout, "kb_base_dir", return_value=root),
            contextlib.redirect_stdout(stdout),
        ):
            rc = call_main(
                kb_closeout.main,
                [
                    "--query",
                    "demo rag query",
                    "--hit-count",
                    "2",
                    "--reason",
                    "hit but not adopted",
                ],
            )

        assert rc == 0
        rows = read_jsonl(root / "_meta" / "closeout.jsonl")
        assert rows[0]["rag_calls"] == 1
        assert rows[0]["rag_calls_inferred"] is True


def test_closeout_requires_reason_when_no_action_ids_exist() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        ctx = make_context(root)
        stderr = io.StringIO()
        with (
            patch.object(kb_closeout, "resolve_context", return_value=ctx),
            patch.object(kb_closeout, "kb_base_dir", return_value=root),
            contextlib.redirect_stderr(stderr),
        ):
            rc = call_main(
                kb_closeout.main,
                [
                    "--query",
                    "demo rag query",
                    "--hit-count",
                    "1",
                    "--rag-calls",
                    "1",
                ],
            )

        assert rc == 2
        assert "reason/skipped_reason is required" in stderr.getvalue()


def test_closeout_rejects_used_ids_outside_allowed_hits() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        ctx = make_context(root)
        stderr = io.StringIO()
        with (
            patch.object(kb_closeout, "resolve_context", return_value=ctx),
            patch.object(kb_closeout, "kb_base_dir", return_value=root),
            contextlib.redirect_stderr(stderr),
        ):
            rc = call_main(
                kb_closeout.main,
                [
                    "--query",
                    "demo rag query",
                    "--hit-count",
                    "1",
                    "--rag-calls",
                    "1",
                    "--allowed-hit-id",
                    "entry-2",
                    "--used",
                    "entry-1",
                ],
            )

        assert rc == 2
        assert "must belong to allowed hit ids" in stderr.getvalue()


def test_closeout_skips_empty_auto_session_brief() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        ctx = make_context(root)
        write_jsonl(
            ctx.kb_path,
            [
                {
                    "id": "entry-1",
                    "ts": "2026-01-01T00:00:00+00:00",
                    "kind": "implementation",
                    "repo": "demo",
                    "branch": "main",
                    "title": "usable context",
                    "story": "body",
                    "tags": [],
                    "used_count": 0,
                    "last_used_ts": "",
                }
            ],
        )

        stdout = io.StringIO()
        with (
            patch.object(kb_closeout, "resolve_context", return_value=ctx),
            patch.object(kb_closeout.kb_update, "resolve_context", return_value=ctx),
            patch.object(kb_closeout, "kb_base_dir", return_value=root),
            patch.object(kb_session_brief, "resolve_context", return_value=ctx),
            contextlib.redirect_stdout(stdout),
        ):
            rc = call_main(
                kb_closeout.main,
                [
                    "--hit-count",
                    "1",
                    "--rag-calls",
                    "1",
                    "--used",
                    "entry-1",
                    "--auto-session-brief",
                    "--verbose",
                ],
            )

        assert rc == 0
        summary = json.loads(stdout.getvalue())
        assert summary["session_briefs_written"] == 0
        closeout_rows = read_jsonl(root / "_meta" / "closeout.jsonl")
        assert closeout_rows[0]["session_brief_ids"] == []
        assert closeout_rows[0]["session_brief_skipped_reason"] == "explicit session brief summary required"


def test_closeout_does_not_turn_query_or_reason_into_session_brief() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        ctx = make_context(root)
        stdout = io.StringIO()
        with (
            patch.object(kb_closeout, "resolve_context", return_value=ctx),
            patch.object(kb_closeout, "kb_base_dir", return_value=root),
            contextlib.redirect_stdout(stdout),
        ):
            rc = call_main(
                kb_closeout.main,
                [
                    "--query",
                    "query text is not a reusable brief",
                    "--hit-count",
                    "1",
                    "--reason",
                    "skipped reason is audit metadata, not a brief summary",
                    "--auto-session-brief",
                    "--verbose",
                ],
            )

        assert rc == 0
        summary = json.loads(stdout.getvalue())
        assert summary["session_briefs_written"] == 0
        assert not (root / "_meta" / "session_briefs.jsonl").exists()
        event = read_jsonl(root / "_meta" / "closeout.jsonl")[0]
        assert event["session_brief_ids"] == []
        assert event["session_brief_skipped_reason"] == "explicit session brief summary required"


def test_closeout_records_session_brief_adoption_without_heating_it() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        ctx = make_context(root)
        write_jsonl(
            root / "_meta" / "session_briefs.jsonl",
            [
                {
                    "id": "brief-1",
                    "event": "kb_session_brief",
                    "kind": "session_brief",
                    "repo": "demo",
                    "branch": "main",
                    "summary": "short-lived context",
                }
            ],
        )

        stdout = io.StringIO()
        with (
            patch.object(kb_closeout, "resolve_context", return_value=ctx),
            patch.object(kb_closeout, "kb_base_dir", return_value=root),
            patch.object(kb_closeout.kb_update, "main", side_effect=AssertionError("brief must not be heated")),
            contextlib.redirect_stdout(stdout),
        ):
            rc = call_main(
                kb_closeout.main,
                [
                    "--query",
                    "recent brief lookup",
                    "--hit-count",
                    "1",
                    "--allowed-hit-id",
                    "brief-1",
                    "--used",
                    "brief-1",
                    "--verbose",
                ],
            )

        assert rc == 0
        summary = json.loads(stdout.getvalue())
        assert summary["heated"] == 0
        assert summary["heat_failed"] == 0
        event = read_jsonl(root / "_meta" / "closeout.jsonl")[0]
        assert event["used_entry_ids"] == []
        assert event["session_brief_used_entry_ids"] == ["brief-1"]
        assert event["session_brief_hit"] is True
        assert event["session_brief_help"] is True
        assert event["heat_applied"] is False
        assert event["heated_entry_ids"] == []


def test_closeout_success_is_quiet_by_default() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        ctx = make_context(root)
        stdout = io.StringIO()
        stderr = io.StringIO()
        with (
            patch.object(kb_closeout, "resolve_context", return_value=ctx),
            patch.object(kb_closeout, "kb_base_dir", return_value=root),
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
        ):
            rc = call_main(kb_closeout.main, [
                "--query", "quiet closeout", "--hit-count", "0", "--reason", "no adopted entries",
            ])

        assert rc == 0
        assert stdout.getvalue() == ""
        assert stderr.getvalue() == ""
        assert read_jsonl(root / "_meta" / "closeout.jsonl")[0]["event"] == "kb_closeout"


def test_closeout_stdout_emits_full_event() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        ctx = make_context(root)
        stdout = io.StringIO()
        with (
            patch.object(kb_closeout, "resolve_context", return_value=ctx),
            patch.object(kb_closeout, "kb_base_dir", return_value=root),
            contextlib.redirect_stdout(stdout),
        ):
            rc = call_main(kb_closeout.main, [
                "--query", "full event", "--hit-count", "0", "--reason", "no adopted entries", "--stdout",
            ])

        assert rc == 0
        event = json.loads(stdout.getvalue())
        assert event["event"] == "kb_closeout"
        assert event["queries"] == ["full event"]
        assert "adoption_effects" in event


def test_closeout_debug_emits_full_event_with_routing_metadata() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        ctx = make_context(root)
        stdout = io.StringIO()
        with (
            patch.object(kb_closeout, "resolve_context", return_value=ctx),
            patch.object(kb_closeout, "kb_base_dir", return_value=root),
            contextlib.redirect_stdout(stdout),
        ):
            rc = call_main(kb_closeout.main, [
                "--query", "debug event", "--hit-count", "0", "--reason", "no adopted entries", "--debug",
            ])

        assert rc == 0
        event = json.loads(stdout.getvalue())
        assert event["routing"] == {"source": "test", "candidate_repos": []}


def test_closeout_heat_failure_is_actionable_and_nonzero() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        ctx = make_context(root)
        stdout = io.StringIO()
        stderr = io.StringIO()
        with (
            patch.object(kb_closeout, "resolve_context", return_value=ctx),
            patch.object(kb_closeout.kb_update, "resolve_context", return_value=ctx),
            patch.object(kb_closeout, "kb_base_dir", return_value=root),
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
        ):
            rc = call_main(kb_closeout.main, [
                "--query", "missing adopted entry",
                "--hit-count", "1",
                "--allowed-hit-id", "missing-entry",
                "--used-fix", "missing-entry",
            ])

        assert rc == 3
        assert stdout.getvalue() == ""
        error = json.loads(stderr.getvalue())
        assert error["status"] == "partial_failure"
        assert error["error"] == "adopted_entry_heat_failed"
        assert error["heat_failed_entry_ids"] == ["missing-entry"]
        assert error["heated_entry_ids"] == []
        assert error["side_effects_applied"] is False
        assert error["failed_side_effects_unknown"] is True
        assert error["retry_safe"] is False
        assert "Do not automatically retry" in error["recovery"]
        event = read_jsonl(root / "_meta" / "closeout.jsonl")[0]
        assert event["heat_failed_entry_ids"] == ["missing-entry"]
        assert "entry not found" in event["heat_errors"][0]["message"].lower()


def test_closeout_partial_heat_failure_warns_against_full_retry() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        ctx = make_context(root)
        write_jsonl(ctx.kb_path, [{
            "id": "entry-ok",
            "ts": "2026-01-01T00:00:00+00:00",
            "kind": "issue",
            "repo": "demo",
            "branch": "main",
            "title": "successful adoption",
            "story": "body",
            "used_count": 0,
            "last_used_ts": "",
        }])
        stderr = io.StringIO()
        with (
            patch.object(kb_closeout, "resolve_context", return_value=ctx),
            patch.object(kb_closeout.kb_update, "resolve_context", return_value=ctx),
            patch.object(kb_closeout, "kb_base_dir", return_value=root),
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(stderr),
        ):
            rc = call_main(kb_closeout.main, [
                "--query", "mixed adoption",
                "--hit-count", "2",
                "--allowed-hit-id", "entry-ok",
                "--allowed-hit-id", "entry-missing",
                "--used-fix", "entry-ok",
                "--used-fix", "entry-missing",
            ])

        assert rc == 3
        error = json.loads(stderr.getvalue())
        assert error["heated_entry_ids"] == ["entry-ok"]
        assert error["heat_failed_entry_ids"] == ["entry-missing"]
        assert error["side_effects_applied"] is True
        assert error["retry_safe"] is False
        assert "Do not automatically retry" in error["recovery"]
        assert read_jsonl(ctx.kb_path)[0]["used_count"] == 0
        assert read_jsonl(root / "repos" / "_meta" / "adoption_events.jsonl")[0]["entry_id"] == "entry-ok"


def test_closeout_write_failure_is_actionable_and_nonzero() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        ctx = make_context(root)
        stdout = io.StringIO()
        stderr = io.StringIO()
        with (
            patch.object(kb_closeout, "resolve_context", return_value=ctx),
            patch.object(kb_closeout, "kb_base_dir", return_value=root),
            patch.object(kb_closeout, "append_jsonl", side_effect=OSError("lock is damaged")),
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
        ):
            rc = call_main(kb_closeout.main, [
                "--query", "write failure", "--hit-count", "0", "--reason", "no adopted entries",
            ])

        assert rc == 1
        assert stdout.getvalue() == ""
        error = json.loads(stderr.getvalue())
        assert error["status"] == "error"
        assert error["error"] == "closeout_write_failed"
        assert "lock is damaged" in error["message"]
        assert error["side_effects_applied"] is False
        assert error["retry_safe"] is True


def test_closeout_write_failure_after_heat_warns_against_retry() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        ctx = make_context(root)
        write_jsonl(ctx.kb_path, [{
            "id": "entry-1",
            "ts": "2026-01-01T00:00:00+00:00",
            "kind": "issue",
            "repo": "demo",
            "branch": "main",
            "title": "adopted entry",
            "story": "body",
            "used_count": 0,
            "last_used_ts": "",
        }])
        stderr = io.StringIO()
        with (
            patch.object(kb_closeout, "resolve_context", return_value=ctx),
            patch.object(kb_closeout.kb_update, "resolve_context", return_value=ctx),
            patch.object(kb_closeout, "kb_base_dir", return_value=root),
            patch.object(kb_closeout, "append_jsonl", side_effect=OSError("closeout storage unavailable")),
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(stderr),
        ):
            rc = call_main(kb_closeout.main, [
                "--query", "adopted entry",
                "--hit-count", "1",
                "--allowed-hit-id", "entry-1",
                "--used-fix", "entry-1",
            ])

        assert rc == 1
        error = json.loads(stderr.getvalue())
        assert error["side_effects_applied"] is True
        assert error["heated_entry_ids"] == ["entry-1"]
        assert error["retry_safe"] is False
        assert "Do not automatically retry" in error["recovery"]
        assert read_jsonl(ctx.kb_path)[0]["used_count"] == 0
        assert read_jsonl(root / "repos" / "_meta" / "adoption_events.jsonl")[0]["entry_id"] == "entry-1"


def test_closeout_validates_session_brief_before_heating() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        ctx = make_context(root)
        stderr = io.StringIO()
        with (
            patch.object(kb_closeout, "resolve_context", return_value=ctx),
            patch.object(kb_closeout, "kb_base_dir", return_value=root),
            patch.object(kb_closeout.kb_update, "main", side_effect=AssertionError("invalid brief must fail before heat")),
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(stderr),
        ):
            rc = call_main(kb_closeout.main, [
                "--query", "invalid brief",
                "--hit-count", "1",
                "--allowed-hit-id", "entry-1",
                "--used-fix", "entry-1",
                "--session-brief-json", "{",
            ])

        assert rc == 2
        assert "invalid --session-brief-json" in stderr.getvalue()
        assert not (root / "_meta" / "closeout.jsonl").exists()


def test_closeout_rejects_nonobject_session_brief_file_before_heating() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        ctx = make_context(root)
        payload_path = root / "brief.json"
        payload_path.write_text("[1]", encoding="utf-8")
        stderr = io.StringIO()
        with (
            patch.object(kb_closeout, "resolve_context", return_value=ctx),
            patch.object(kb_closeout, "kb_base_dir", return_value=root),
            patch.object(kb_closeout.kb_update, "main", side_effect=AssertionError("invalid brief file must fail before heat")),
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(stderr),
        ):
            rc = call_main(kb_closeout.main, [
                "--query", "invalid brief file",
                "--hit-count", "1",
                "--allowed-hit-id", "entry-1",
                "--used-fix", "entry-1",
                "--session-brief-json-file", str(payload_path),
            ])

        assert rc == 2
        assert "must contain a JSON object" in stderr.getvalue()
        assert not (root / "_meta" / "closeout.jsonl").exists()


def test_archive_protects_current_or_recent_records() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir) / "repos"
        kb_path = root / "demo" / "main" / "experience" / "kb.jsonl"
        write_jsonl(
            kb_path,
            [
                {"id": "missing-status", "ts": "2025-01-01T00:00:00+00:00"},
                {"id": "current", "status": "current", "ts": "2025-01-01T00:00:00+00:00"},
                {"id": "old-noncurrent", "status": "superseded", "ts": "2025-01-01T00:00:00+00:00"},
                {
                    "id": "recent-update",
                    "status": "obsolete",
                    "ts": "2025-01-01T00:00:00+00:00",
                    "updated_ts": "2026-07-02T00:00:00+00:00",
                },
                {
                    "id": "recent-use",
                    "status": "historical",
                    "ts": "2025-01-01T00:00:00+00:00",
                    "last_used_ts": "2026-07-03T00:00:00+00:00",
                },
                {"id": "missing-date", "status": "archived"},
                {
                    "id": "all-old",
                    "status": "obsolete",
                    "ts": "2025-01-01T00:00:00+00:00",
                    "updated_ts": "2025-06-01T00:00:00+00:00",
                    "last_used_ts": "2025-12-01T00:00:00+00:00",
                },
            ],
        )

        stats = kb_archive_old_records.archive_old_records(root, "2026-07-01", apply=True)

        assert stats["entries_seen"] == 7
        assert stats["entries_archived"] == 2
        assert stats["entries_kept"] == 5
        assert stats["entries_protected"] == 5
        assert stats["protected_by_reason"] == {
            "current_status": 2,
            "recent_activity": 2,
            "missing_activity_date": 1,
        }
        assert {row["id"] for row in read_jsonl(kb_path)} == {
            "missing-status",
            "current",
            "recent-update",
            "recent-use",
            "missing-date",
        }
        archive_path = Path(temp_dir) / "_archive" / "pre-2026-07-01" / kb_path.relative_to(root)
        archived = read_jsonl(archive_path)
        assert {row["id"] for row in archived} == {"old-noncurrent", "all-old"}


def test_add_defaults_usage_fields() -> None:
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
                    "experience",
                    "--title",
                    "new entry",
                    "--story",
                    "body",
                    "--field",
                    "used_count=7",
                    "--field",
                    "last_used_ts=2026-01-01T00:00:00",
                ],
            )

        assert rc == 0
        rows = read_jsonl(ctx.kb_path)
        assert rows[0]["used_count"] == 0
        assert rows[0]["last_used_ts"] == ""
        summary = json.loads(stdout.getvalue())
        assert summary["id"] == rows[0]["id"]


def test_add_smart_field_check_enforces_durable_quality() -> None:
    def encoded(payload: dict[str, Any]) -> str:
        return base64.b64encode(json.dumps(payload, ensure_ascii=False).encode("utf-8")).decode("ascii")

    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        ctx = make_context(root)
        evidence = Path(ctx.workspace_dir) / "logs" / "service.log"
        evidence.parent.mkdir(parents=True, exist_ok=True)
        evidence.write_text("verified", encoding="utf-8")
        with (
            patch.object(kb_add, "resolve_context", return_value=ctx),
            patch.object(kb_add, "search_related_entries", return_value=[]),
            patch.object(kb_add.Path, "home", return_value=root / "home"),
        ):
            rejected = call_main(kb_add.main, [
                "--entry-b64", encoded({"kind": "issue", "title": "weak", "story": "body"}),
                "--smart-field-check",
            ])
            accepted = call_main(kb_add.main, [
                "--entry-b64", encoded({
                    "kind": "issue",
                    "title": "verified timeout",
                    "story": "root cause and validation",
                    "aliases": ["timeout issue", "service timeout"],
                    "trigger_terms": ["TimeoutException", "requestId", "service.log"],
                    "source_paths": ["logs/service.log"],
                }),
                "--smart-field-check",
            ])
        assert rejected == 3
        assert accepted == 0
        rows = read_jsonl(ctx.kb_path)
        assert len(rows) == 1
        assert rows[0]["status"] == "current"
        assert rows[0]["verified_at"]
        assert rows[0]["evidence_level"] == "documented"


def test_config_declares_search_is_readonly() -> None:
    config_path = Path(__file__).resolve().parent.parent / "config.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))

    assert "touch_on_search" not in config["search"]
    assert config["usage"]["heat_field"] == "used_count"
    assert config["cleanup"]["auto_merge_mode"] in {"ai", "off"}
    assert config["cleanup"]["same_issue_strategy"]["mode"] in {"ai", "off"}


def main() -> int:
    tests = [
        test_search_is_readonly_by_default,
        test_config_cannot_enable_search_logging_without_flag,
        test_search_does_not_inject_aggregation_view_by_default,
        test_relevance_score_ignores_call_count,
        test_update_use_records_runtime_heat_without_rewriting_durable_record,
        test_resolve_context_explicit_repo_and_branch_bypass_child_discovery,
        test_resolve_context_repo_override_keeps_current_git_branch,
        test_resolve_context_ambiguous_multi_repo_falls_back_silently,
        test_closeout_writes_ai_only_audit_event,
        test_closeout_records_locator_help_without_heating,
        test_closeout_rejects_invalid_linked_retrieval_id_before_writes,
        test_closeout_normalizes_duplicate_effects_to_highest_level,
        test_closeout_can_write_recent_session_brief,
        test_closeout_infers_rag_calls_from_hit_count_when_omitted,
        test_closeout_requires_reason_when_no_action_ids_exist,
        test_closeout_rejects_used_ids_outside_allowed_hits,
        test_closeout_skips_empty_auto_session_brief,
        test_closeout_does_not_turn_query_or_reason_into_session_brief,
        test_closeout_records_session_brief_adoption_without_heating_it,
        test_closeout_success_is_quiet_by_default,
        test_closeout_stdout_emits_full_event,
        test_closeout_debug_emits_full_event_with_routing_metadata,
        test_closeout_heat_failure_is_actionable_and_nonzero,
        test_closeout_partial_heat_failure_warns_against_full_retry,
        test_closeout_write_failure_is_actionable_and_nonzero,
        test_closeout_write_failure_after_heat_warns_against_retry,
        test_closeout_validates_session_brief_before_heating,
        test_closeout_rejects_nonobject_session_brief_file_before_heating,
        test_archive_protects_current_or_recent_records,
        test_add_defaults_usage_fields,
        test_add_smart_field_check_enforces_durable_quality,
        test_config_declares_search_is_readonly,
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

    print("kb_p0_usage tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
