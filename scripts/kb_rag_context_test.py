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
    import kb_rag_context
    import kb_search
    import kb_session_brief
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


def call_main(
    func: Callable[[list[str]], int],
    argv: list[str],
    *,
    allow_receipt_write: bool = False,
) -> int:
    def invoke() -> int:
        try:
            return func(argv)
        except SystemExit as exc:
            code = exc.code
            return code if isinstance(code, int) else 1

    if func is kb_rag_context.main and not allow_receipt_write:
        with patch.object(kb_rag_context, "persist_retrieval_receipt"):
            return invoke()
    return invoke()


def test_search_matches_trigger_terms_and_source_paths() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        ctx = make_context(root)
        write_jsonl(
            ctx.kb_path,
            [
                {
                    "id": "entry-1",
                    "ts": "2026-01-01T00:00:00+00:00",
                    "kind": "issue",
                    "repo": "demo",
                    "branch": "main",
                    "title": "startup failure",
                    "story": "short note",
                    "tags": [],
                    "trigger_terms": ["dynamic-datasource Please check the setting of primary"],
                    "source_paths": ["logs/app-startup.log"],
                    "key_files": ["src/main/resources/application.yml"],
                }
            ],
        )

        stdout = io.StringIO()
        with (
            patch.object(kb_search, "resolve_context", return_value=ctx),
            patch.object(kb_search, "global_bucket_dir", return_value=root / "global"),
            patch.object(kb_search, "InvertedIndex", None),
            patch.object(kb_search, "get_inverted_index_path", None),
            contextlib.redirect_stdout(stdout),
        ):
            rc = call_main(kb_search.main, ["app-startup.log", "--json"])

        assert rc == 0
        rows = json.loads(stdout.getvalue())
        assert [row["id"] for row in rows] == ["entry-1"]


def test_rag_context_is_compact_and_read_only() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        ctx = make_context(root)
        long_story = "root cause " + ("very long detail " * 80)
        write_jsonl(
            ctx.kb_path,
            [
                {
                    "id": "entry-1",
                    "ts": "2026-01-01T00:00:00+00:00",
                    "kind": "issue",
                    "repo": "demo",
                    "branch": "main",
                    "title": "dynamic datasource primary missing",
                    "story": long_story,
                    "tags": ["datasource"],
                    "trigger_terms": ["dynamic-datasource Please check the setting of primary"],
                    "source_paths": ["logs/app-startup.log"],
                    "used_count": 0,
                    "last_used_ts": "",
                }
            ],
        )
        before = ctx.kb_path.read_text(encoding="utf-8")

        stdout = io.StringIO()
        with (
            patch.object(kb_search, "resolve_context", return_value=ctx),
            patch.object(kb_search, "global_bucket_dir", return_value=root / "global"),
            patch.object(kb_search, "InvertedIndex", None),
            patch.object(kb_search, "get_inverted_index_path", None),
            contextlib.redirect_stdout(stdout),
        ):
            rc = call_main(
                kb_rag_context.main,
                [
                    "dynamic-datasource primary",
                    "--retrieval-id",
                    "test-retrieval-1",
                    "--json",
                    "--max-snippet-chars",
                    "120",
                    "--no-session-briefs",
                ],
            )

        assert rc == 0
        payload = json.loads(stdout.getvalue())
        assert payload["mode"] == "read_only_rag_context"
        assert payload["retrieval_id"] == "test-retrieval-1"
        assert payload["hit_count"] == 1
        item = payload["items"][0]
        assert item["entry_id"] == "entry-1"
        assert item["record_rev"]
        assert len(item["summary"]) <= 120
        assert "story" not in item
        assert "trigger_terms" in item["matched_fields"]
        assert ctx.kb_path.read_text(encoding="utf-8") == before


def test_rag_context_rejects_invalid_retrieval_id() -> None:
    stderr = io.StringIO()
    with contextlib.redirect_stderr(stderr):
        rc = call_main(
            kb_rag_context.main,
            ["demo", "--retrieval-id", "contains whitespace", "--json"],
        )
    assert rc == 2
    assert "--retrieval-id must be" in stderr.getvalue()


def test_rag_context_surfaces_prior_outcome_feedback_without_cross_project_paths() -> None:
    local_item = {
        "entry_id": "entry-1",
        "repo": "demo",
        "branch": "main",
        "record_rev": "rev-2",
    }
    cross_project_item = {
        "entry_id": "entry-2",
        "repo": "other",
        "branch": "main",
        "record_rev": "rev-1",
        "cross_project": True,
    }
    feedback = {
        ("demo", "main", "entry-1"): {
            "event_count": 2,
            "accepted_count": 1,
            "rejected_count": 1,
            "recurrence_observed_count": 1,
            "recurrence_not_observed_count": 1,
            "last_event": {
                "event_id": "outcome-2",
                "record_rev": "rev-1",
                "actual_result": "failed again",
                "evidence_paths": [r"F:\private\audit.json"],
                "recurrence": "observed",
                "user_verdict": "rejected",
            },
        },
        ("other", "main", "entry-2"): {
            "event_count": 1,
            "accepted_count": 1,
            "rejected_count": 0,
            "recurrence_observed_count": 0,
            "recurrence_not_observed_count": 1,
            "last_event": {
                "event_id": "outcome-3",
                "record_rev": "rev-1",
                "actual_result": "private detail",
                "evidence_paths": [r"F:\other-project\audit.json"],
                "recurrence": "not_observed",
                "user_verdict": "accepted",
            },
        },
    }
    with patch.object(
        kb_rag_context.kb_outcome_event,
        "outcome_feedback_for_entries",
        return_value=feedback,
    ):
        kb_rag_context._attach_outcome_feedback([local_item, cross_project_item])

    assert local_item["outcome_feedback"]["last_event"]["evidence_paths"]
    assert "prior outcome feedback requires recheck" in local_item["warning"]
    assert "earlier record revision" in local_item["warning"]
    cross_last = cross_project_item["outcome_feedback"]["last_event"]
    assert "actual_result" not in cross_last
    assert "evidence_paths" not in cross_last


def _receipt_payload(
    *,
    retrieval_id: str = "receipt-1",
    query: str = "investigate alpha component",
) -> dict[str, Any]:
    return {
        "retrieval_id": retrieval_id,
        "query": query,
        "repo": "demo",
        "branch": "main",
        "items": [
            {
                "entry_id": "entry-1",
                "record_rev": "rev-1",
                "freshness_state": "fresh",
            }
        ],
    }


def test_rag_context_persists_strict_receipt_and_atomic_output_before_stdout() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        log_path = root / "repos" / "_meta" / "retrieval_receipts.jsonl"
        output_path = root / "receipt.json"
        stdout = io.StringIO()
        with (
            patch.object(
                kb_rag_context,
                "build_context",
                return_value=_receipt_payload(
                    query="investigate alpha component in failed state",
                ),
            ),
            patch.object(kb_rag_context, "retrieval_receipts_path", return_value=log_path),
            patch.object(kb_rag_context, "now_iso", return_value="2026-08-03T20:00:00+08:00"),
            contextlib.redirect_stdout(stdout),
        ):
            rc = call_main(
                kb_rag_context.main,
                [
                    "investigate alpha component in failed state",
                    "--retrieval-id",
                    "receipt-1",
                    "--scope-anchor",
                    "component:alpha",
                    "--scope-anchor",
                    "state:failed",
                    "--receipt-output",
                    str(output_path),
                    "--json",
                ],
                allow_receipt_write=True,
            )

        assert rc == 0
        assert json.loads(stdout.getvalue())["retrieval_id"] == "receipt-1"
        rows = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
        assert len(rows) == 1
        receipt = rows[0]
        assert set(receipt) == {
            "schema",
            "retrieval_id",
            "query",
            "repo",
            "branch",
            "scope_anchors",
            "hits",
            "created_at",
        }
        assert receipt["schema"] == "personal-kb.retrieval-receipt/v1"
        assert receipt["scope_anchors"] == ["component:alpha", "state:failed"]
        assert receipt["hits"] == [
            {
                "entry_id": "entry-1",
                "record_rev": "rev-1",
                "freshness_state": "fresh",
            }
        ]
        assert json.loads(output_path.read_text(encoding="utf-8")) == receipt


def test_retrieval_receipt_retry_is_idempotent_and_conflict_is_rejected() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        base_dir = Path(temp_dir) / "repos"
        first = kb_rag_context.persist_retrieval_receipt(
            _receipt_payload(),
            scope_anchors=["component:alpha"],
            base_dir=base_dir,
            created_at="2026-08-03T20:00:00+08:00",
        )
        retried = kb_rag_context.persist_retrieval_receipt(
            _receipt_payload(),
            scope_anchors=["component:alpha"],
            base_dir=base_dir,
            created_at="2026-08-03T20:01:00+08:00",
        )
        receipt_log = kb_rag_context.retrieval_receipts_path(base_dir)
        rows = [
            json.loads(line)
            for line in receipt_log.read_text(encoding="utf-8").splitlines()
        ]
        assert retried == first
        assert rows == [first]

        try:
            kb_rag_context.persist_retrieval_receipt(
                _receipt_payload(query="different alpha query"),
                scope_anchors=["component:alpha"],
                base_dir=base_dir,
            )
        except kb_rag_context.IdempotencyConflictError:
            pass
        else:
            raise AssertionError("conflicting retrieval_id was accepted")


def test_retrieval_receipt_rejects_unrelated_query_with_fake_scope_anchors() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        base_dir = Path(temp_dir) / "repos"
        attempts = [
            (
                "prepare the quarterly budget report",
                ["component:alpha", "state:failed"],
                "component:alpha",
            ),
            (
                "investigate alpha component",
                ["component:alpha", "state:failed"],
                "state:failed",
            ),
        ]
        for query, anchors, rejected_anchor in attempts:
            try:
                kb_rag_context.persist_retrieval_receipt(
                    _receipt_payload(query=query),
                    scope_anchors=anchors,
                    base_dir=base_dir,
                )
            except ValueError as exc:
                assert rejected_anchor in str(exc)
                assert "not explicitly bound" in str(exc)
            else:
                raise AssertionError("query with an unbound scope anchor was accepted")
        assert not kb_rag_context.retrieval_receipts_path(base_dir).exists()


def test_rag_context_cli_rejects_unbound_scope_anchor_before_stdout() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        log_path = Path(temp_dir) / "repos" / "_meta" / "retrieval_receipts.jsonl"
        stdout = io.StringIO()
        stderr = io.StringIO()
        with (
            patch.object(
                kb_rag_context,
                "build_context",
                return_value=_receipt_payload(query="prepare the quarterly budget report"),
            ) as build_context,
            patch.object(kb_rag_context, "retrieval_receipts_path", return_value=log_path),
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
        ):
            rc = call_main(
                kb_rag_context.main,
                [
                    "prepare the quarterly budget report",
                    "--scope-anchor",
                    "component:alpha",
                    "--json",
                ],
                allow_receipt_write=True,
            )

        assert rc == 2
        build_context.assert_not_called()
        assert stdout.getvalue() == ""
        assert "not explicitly bound" in stderr.getvalue()
        assert not log_path.exists()


def test_retrieval_receipt_accepts_unicode_path_and_hyphen_scope_bindings() -> None:
    cases = [
        ("复核角色甲的当前角色状态", ["character:角色甲"]),
        (
            r"inspect F:\workspace\alpha-beta\config.yaml before release",
            [r"F:\workspace\alpha-beta\config.yaml"],
        ),
        ("Investigate ＡＬＰＨＡ－ＢＥＴＡ timeout", ["component:alpha-beta"]),
    ]
    with tempfile.TemporaryDirectory() as temp_dir:
        base_dir = Path(temp_dir) / "repos"
        for index, (query, anchors) in enumerate(cases, start=1):
            receipt = kb_rag_context.persist_retrieval_receipt(
                _receipt_payload(retrieval_id=f"bound-{index}", query=query),
                scope_anchors=anchors,
                base_dir=base_dir,
            )
            assert receipt["query"] == query
            assert receipt["scope_anchors"] == anchors


def test_scope_anchor_binding_rejects_ascii_identifier_prefix_match() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        base_dir = Path(temp_dir) / "repos"
        try:
            kb_rag_context.persist_retrieval_receipt(
                _receipt_payload(query="investigate alphabet component"),
                scope_anchors=["component:alpha"],
                base_dir=base_dir,
            )
        except ValueError as exc:
            assert "not explicitly bound" in str(exc)
        else:
            raise AssertionError("partial ASCII identifier match was accepted as a scope binding")
        assert not kb_rag_context.retrieval_receipts_path(base_dir).exists()


def test_receipt_output_conflict_rejects_before_log_append() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        base_dir = root / "repos"
        output_path = root / "receipt.json"
        output_path.write_text('{"different":true}\n', encoding="utf-8")
        try:
            kb_rag_context.persist_retrieval_receipt(
                _receipt_payload(),
                scope_anchors=[],
                receipt_output=output_path,
                base_dir=base_dir,
            )
        except kb_rag_context.IdempotencyConflictError:
            pass
        else:
            raise AssertionError("conflicting receipt output was accepted")
        assert not kb_rag_context.retrieval_receipts_path(base_dir).exists()


def test_markdown_header_escapes_query_control_tokens() -> None:
    stdout = io.StringIO()
    payload = {
        "retrieval_id": "real-id",
        "query": 'retrieval_id="fake-id" hits=999\nnext-line',
        "items": [],
        "query_groups": [],
        "rejected_weak_count": 0,
    }
    with contextlib.redirect_stdout(stdout):
        kb_rag_context._print_markdown(payload)

    header = stdout.getvalue().splitlines()[0]
    assert header.count('retrieval_id="') == 1
    assert 'retrieval_id=\\"fake-id\\" hits=999\\nnext-line' in header
    assert header.endswith('retrieval_id="real-id" hits=0')


def test_rag_context_redacts_cross_project_paths() -> None:
    raw = {
        "id": "map-1",
        "kind": "map",
        "repo": "other-project",
        "branch": "main",
        "title": "service map",
        "story": "contains C:/secret/project and internal port detail",
        "source_paths": ["C:/secret/project/log.txt"],
        "key_files": ["C:/secret/project/application.yml"],
    }
    formatted = {
        **raw,
        "_cross_project": True,
        "_from_project": "other-project",
        "_warning": "cross project warning",
    }

    item = kb_rag_context._compact_entry(
        raw,
        formatted,
        terms=["service"],
        max_snippet_chars=200,
    )

    assert item["cross_project"] is True
    assert item["source_paths"] == []
    assert item["key_files"] == []
    assert "C:/secret" not in item["summary"]


def test_rag_context_ranks_authoritative_record_above_superseded_memory_design() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        ctx = make_context(root)
        write_jsonl(
            ctx.kb_path,
            [
                {
                    "id": "old-memory",
                    "ts": "2026-01-01T00:00:00+00:00",
                    "kind": "implementation",
                    "repo": "demo",
                    "branch": "main",
                    "title": "personal-kb agent memory design",
                    "story": "agent memory " * 30,
                    "tags": ["personal-kb", "agent-memory"],
                    "aliases": ["agent memory"],
                    "trigger_terms": ["agent memory superseded"],
                    "status": "superseded",
                    "superseded_by": "new-rag",
                    "used_count": 10,
                },
                {
                    "id": "new-rag",
                    "ts": "2026-01-02T00:00:00+00:00",
                    "kind": "implementation",
                    "repo": "demo",
                    "branch": "main",
                    "title": "personal-kb RAG-first boundary",
                    "story": "default read-only RAG context",
                    "tags": ["personal-kb", "RAG-first"],
                    "aliases": ["personal-kb RAG-first", "agent memory route downgraded"],
                    "trigger_terms": ["RAG-first", "kb_rag_context.py", "agent memory superseded"],
                    "used_count": 0,
                },
            ],
        )

        stdout = io.StringIO()
        with (
            patch.object(kb_search, "resolve_context", return_value=ctx),
            patch.object(kb_search, "global_bucket_dir", return_value=root / "global"),
            patch.object(kb_search, "InvertedIndex", None),
            patch.object(kb_search, "get_inverted_index_path", None),
            contextlib.redirect_stdout(stdout),
        ):
            rc = call_main(
                kb_rag_context.main,
                ["personal-kb agent memory RAG-first", "--json", "--limit", "2", "--include-noncurrent", "--no-session-briefs"],
            )

        assert rc == 0
        payload = json.loads(stdout.getvalue())
        assert [item["entry_id"] for item in payload["items"]] == ["new-rag", "old-memory"]
        assert "superseded_by=new-rag" in payload["items"][1]["warning"]


def test_rag_context_filters_noncurrent_records_by_default() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        ctx = make_context(root)
        write_jsonl(
            ctx.kb_path,
            [
                {
                    "id": "draft-memory",
                    "ts": "2026-01-01T00:00:00+00:00",
                    "kind": "implementation",
                    "repo": "demo",
                    "branch": "main",
                    "title": "personal-kb draft memory layer",
                    "story": "old draft flow",
                    "tags": ["personal-kb"],
                    "trigger_terms": ["personal-kb flow"],
                    "status": "draft_pending_implementation",
                    "used_count": 10,
                },
                {
                    "id": "old-memory",
                    "ts": "2026-01-02T00:00:00+00:00",
                    "kind": "implementation",
                    "repo": "demo",
                    "branch": "main",
                    "title": "personal-kb superseded memory layer",
                    "story": "old superseded flow",
                    "tags": ["personal-kb"],
                    "trigger_terms": ["personal-kb flow"],
                    "status": "superseded",
                    "superseded_by": "new-rag",
                    "used_count": 8,
                },
                {
                    "id": "new-rag",
                    "ts": "2026-01-03T00:00:00+00:00",
                    "kind": "implementation",
                    "repo": "demo",
                    "branch": "main",
                    "title": "personal-kb current RAG flow",
                    "story": "current read-only RAG flow",
                    "tags": ["personal-kb", "RAG-first"],
                    "trigger_terms": ["personal-kb flow"],
                    "used_count": 0,
                },
            ],
        )

        stdout = io.StringIO()
        with (
            patch.object(kb_search, "resolve_context", return_value=ctx),
            patch.object(kb_search, "global_bucket_dir", return_value=root / "global"),
            patch.object(kb_search, "InvertedIndex", None),
            patch.object(kb_search, "get_inverted_index_path", None),
            contextlib.redirect_stdout(stdout),
        ):
            rc = call_main(kb_rag_context.main, ["personal-kb flow", "--json", "--limit", "5", "--no-session-briefs"])

        assert rc == 0
        payload = json.loads(stdout.getvalue())
        assert [item["entry_id"] for item in payload["items"]] == ["new-rag"]


def test_rag_context_plans_task_query_and_filters_weak_story_noise() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        ctx = make_context(root)
        write_jsonl(
            ctx.kb_path,
            [
                {
                    "id": "rag-flow",
                    "ts": "2026-01-03T00:00:00+00:00",
                    "kind": "implementation",
                    "repo": "demo",
                    "branch": "main",
                    "title": "personal-kb RAG-first flow",
                    "story": "use kb_rag_context.py before work and kb_closeout.py after work",
                    "tags": ["personal-kb", "RAG-first"],
                    "aliases": ["personal-kb RAG-first"],
                    "trigger_terms": ["kb_rag_context.py", "kb_closeout.py", "使用后加热记录"],
                    "source_paths": ["skills/personal-kb/SKILL.md"],
                },
                {
                    "id": "noisy-project",
                    "ts": "2026-01-04T00:00:00+00:00",
                    "kind": "experience",
                    "repo": "demo",
                    "branch": "main",
                    "title": "unrelated interview material",
                    "story": "AI agent KB project interview note with closeout mentioned only in prose",
                    "tags": ["interview"],
                    "source_paths": ["interview/demo.md"],
                },
            ],
        )

        stdout = io.StringIO()
        with (
            patch.object(kb_search, "resolve_context", return_value=ctx),
            patch.object(kb_search, "global_bucket_dir", return_value=root / "global"),
            patch.object(kb_search, "InvertedIndex", None),
            patch.object(kb_search, "get_inverted_index_path", None),
            contextlib.redirect_stdout(stdout),
        ):
            rc = call_main(
                kb_rag_context.main,
                ["子 agent KB 父 会话 scout closeout", "--json", "--limit", "5", "--no-session-briefs"],
            )

        assert rc == 0
        payload = json.loads(stdout.getvalue())
        assert [item["entry_id"] for item in payload["items"]] == ["rag-flow"]
        assert payload["rejected_weak_count"] >= 1
        assert any(group["name"] == "closeout" for group in payload["query_groups"])


def test_rag_context_recognizes_unspaced_chinese_kbskill_query() -> None:
    ctx = make_context(Path("/tmp/demo"))
    raw_results = [
        {
            "id": "kb-optimization",
            "kind": "implementation",
            "repo": "demo",
            "branch": "main",
            "title": "personal-kb skill retrieval optimization",
            "story": "improve lexical retrieval without a vector dependency",
            "tags": ["personal-kb"],
            "aliases": ["personal kb skill"],
            "trigger_terms": ["kb_rag_context.py"],
            "source_paths": ["skills/personal-kb/SKILL.md"],
        }
    ]

    stdout = io.StringIO()
    with (
        patch.object(kb_session_brief, "search_recent_briefs", return_value=[]),
        patch.object(kb_search, "_search_once", return_value=(raw_results, ctx)),
        patch.object(kb_search, "_format_cross_project_entry", side_effect=lambda entry, _repo: dict(entry)),
        contextlib.redirect_stdout(stdout),
    ):
        rc = call_main(
            kb_rag_context.main,
            ["思考我的kbskill还应该如何优化", "--json", "--no-session-briefs", "--limit", "2"],
        )

    assert rc == 0
    payload = json.loads(stdout.getvalue())
    assert [item["entry_id"] for item in payload["items"]] == ["kb-optimization"]
    assert any(group["name"] == "personal-kb" for group in payload["query_groups"])


def test_rag_context_accepts_planner_only_concept_match_but_rejects_story_noise() -> None:
    ctx = make_context(Path("/tmp/demo"))
    raw_results = [
        {
            "id": "closeout-runtime",
            "kind": "implementation",
            "repo": "demo",
            "branch": "main",
            "title": "personal-kb closeout runtime",
            "story": "durable adoption handling",
            "tags": ["personal-kb"],
            "trigger_terms": ["closeout", "kb_closeout.py"],
            "source_paths": ["skills/personal-kb/scripts/kb_closeout.py"],
        },
        {
            "id": "story-noise",
            "kind": "experience",
            "repo": "demo",
            "branch": "main",
            "title": "unrelated note",
            "story": "closeout is mentioned only in a weak project story",
            "tags": ["interview"],
        },
    ]

    stdout = io.StringIO()
    with (
        patch.object(kb_session_brief, "search_recent_briefs", return_value=[]),
        patch.object(kb_search, "_search_once", return_value=(raw_results, ctx)),
        patch.object(kb_search, "_format_cross_project_entry", side_effect=lambda entry, _repo: dict(entry)),
        contextlib.redirect_stdout(stdout),
    ):
        rc = call_main(
            kb_rag_context.main,
            ["知识库使用后应该怎么处理", "--json", "--no-session-briefs", "--limit", "5"],
        )

    assert rc == 0
    payload = json.loads(stdout.getvalue())
    assert [item["entry_id"] for item in payload["items"]] == ["closeout-runtime"]
    assert payload["rejected_weak_count"] >= 1


def test_rag_context_keeps_precise_project_hits_after_weak_filtering() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        ctx = make_context(root)
        write_jsonl(
            ctx.kb_path,
            [
                {
                    "id": "orion-material",
                    "ts": "2026-01-03T00:00:00+00:00",
                    "kind": "experience",
                    "repo": "demo",
                    "branch": "main",
                    "title": "orion-service Java 后端迁移文档",
                    "story": "Java backend migration material",
                    "tags": ["orion-service", "Java"],
                    "aliases": ["ORION"],
                    "trigger_terms": ["orion-service"],
                    "source_paths": ["docs/orion-service-migration.md"],
                },
                {
                    "id": "generic-material",
                    "ts": "2026-01-04T00:00:00+00:00",
                    "kind": "experience",
                    "repo": "demo",
                    "branch": "main",
                    "title": "generic Java project material",
                    "story": "Java 后端 面试材料",
                    "tags": ["interview"],
                    "source_paths": ["docs/generic.md"],
                },
            ],
        )

        stdout = io.StringIO()
        with (
            patch.object(kb_search, "resolve_context", return_value=ctx),
            patch.object(kb_search, "global_bucket_dir", return_value=root / "global"),
            patch.object(kb_search, "InvertedIndex", None),
            patch.object(kb_search, "get_inverted_index_path", None),
            contextlib.redirect_stdout(stdout),
        ):
            rc = call_main(
                kb_rag_context.main,
                ["orion-service Java 后端 迁移文档", "--json", "--limit", "5", "--no-session-briefs"],
            )

        assert rc == 0
        payload = json.loads(stdout.getvalue())
        assert [item["entry_id"] for item in payload["items"]] == ["orion-material"]


def test_rag_context_reads_recent_session_briefs_before_long_term_kb() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        ctx = make_context(root)
        write_jsonl(
            ctx.kb_path,
            [
                {
                    "id": "long-term",
                    "ts": "2026-01-01T00:00:00+00:00",
                    "kind": "implementation",
                    "repo": "demo",
                    "branch": "main",
                    "title": "long term RAG flow",
                    "story": "durable RAG rule",
                    "tags": ["personal-kb", "RAG-first"],
                    "trigger_terms": ["kb_rag_context.py"],
                    "source_paths": ["skills/personal-kb/SKILL.md"],
                }
            ],
        )
        write_jsonl(
            root / "repos" / "_meta" / "session_briefs.jsonl",
            [
                kb_session_brief.build_brief(
                    title="recent closeout correction",
                    summary="user just corrected the closeout and current entrypoint route",
                    repo="demo",
                    branch="main",
                    cwd=str(root / "workspace"),
                    tags=["recent-session", "personal-kb"],
                    anchors=["closeout", "entrypoint"],
                    queries=["closeout entrypoint personal-kb"],
                    used_entry_ids=[],
                    written_entry_ids=[],
                    updated_entry_ids=[],
                    source="codex",
                    session_id="s-1",
                )
            ],
        )

        stdout = io.StringIO()
        with (
            patch.object(kb_search, "resolve_context", return_value=ctx),
            patch.object(kb_search, "global_bucket_dir", return_value=root / "global"),
            patch.object(kb_search, "InvertedIndex", None),
            patch.object(kb_search, "get_inverted_index_path", None),
            patch.object(kb_session_brief, "resolve_context", return_value=ctx),
            patch.object(kb_session_brief, "kb_base_dir", return_value=root / "repos"),
            contextlib.redirect_stdout(stdout),
        ):
            rc = call_main(
                kb_rag_context.main,
                ["closeout entrypoint personal-kb", "--json", "--limit", "5"],
            )

        assert rc == 0
        payload = json.loads(stdout.getvalue())
        assert payload["items"][0]["kind"] == "session_brief"
        assert payload["items"][0]["context_layer"] == "recent_session"
        assert payload["items"][0]["query_groups"] == ["recent-session"]
        assert any(group["name"] == "recent-session" for group in payload["query_groups"])


def test_rag_context_filters_by_min_confidence() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        ctx = make_context(root)
        write_jsonl(
            ctx.kb_path,
            [
                {
                    "id": "strong-hit",
                    "ts": "2026-01-01T00:00:00+00:00",
                    "kind": "issue",
                    "repo": "demo",
                    "branch": "main",
                    "title": "dynamic datasource timeout",
                    "story": "primary datasource timeout",
                    "trigger_terms": ["dynamic-datasource", "primary"],
                    "source_paths": ["logs/app.log"],
                },
                {
                    "id": "weak-hit",
                    "ts": "2026-01-02T00:00:00+00:00",
                    "kind": "experience",
                    "repo": "demo",
                    "branch": "main",
                    "title": "generic note",
                    "story": "dynamic-datasource primary timeout only mentioned in prose",
                },
            ],
        )

        stdout = io.StringIO()
        with (
            patch.object(kb_search, "resolve_context", return_value=ctx),
            patch.object(kb_search, "global_bucket_dir", return_value=root / "global"),
            patch.object(kb_search, "InvertedIndex", None),
            patch.object(kb_search, "get_inverted_index_path", None),
            contextlib.redirect_stdout(stdout),
        ):
            rc = call_main(
                kb_rag_context.main,
                [
                    "dynamic-datasource primary",
                    "--json",
                    "--include-weak",
                    "--min-confidence",
                    "0.75",
                    "--no-session-briefs",
                ],
            )

        assert rc == 0
        payload = json.loads(stdout.getvalue())
        assert [item["entry_id"] for item in payload["items"]] == ["strong-hit"]
        assert payload["filtered_low_confidence_count"] >= 1


def test_rag_context_applies_max_total_chars_stably() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        ctx = make_context(root)
        long_story = "timeout detail " * 50
        write_jsonl(
            ctx.kb_path,
            [
                {
                    "id": "entry-1",
                    "ts": "2026-01-01T00:00:00+00:00",
                    "kind": "issue",
                    "repo": "demo",
                    "branch": "main",
                    "title": "order timeout entry one",
                    "story": long_story,
                    "trigger_terms": ["order-timeout"],
                    "source_paths": ["logs/one.log"],
                },
                {
                    "id": "entry-2",
                    "ts": "2026-01-02T00:00:00+00:00",
                    "kind": "issue",
                    "repo": "demo",
                    "branch": "main",
                    "title": "order timeout entry two",
                    "story": long_story,
                    "trigger_terms": ["order-timeout"],
                    "source_paths": ["logs/two.log"],
                },
            ],
        )

        stdout = io.StringIO()
        with (
            patch.object(kb_search, "resolve_context", return_value=ctx),
            patch.object(kb_search, "global_bucket_dir", return_value=root / "global"),
            patch.object(kb_search, "InvertedIndex", None),
            patch.object(kb_search, "get_inverted_index_path", None),
            contextlib.redirect_stdout(stdout),
        ):
            rc = call_main(
                kb_rag_context.main,
                [
                    "order-timeout",
                    "--json",
                    "--limit",
                    "2",
                    "--max-total-chars",
                    "260",
                    "--no-session-briefs",
                ],
            )

        assert rc == 0
        payload = json.loads(stdout.getvalue())
        assert payload["hit_count"] == len(payload["items"])
        assert payload["truncation"]["max_total_chars"] == 260
        assert payload["truncation"]["returned_item_chars"] <= 260
        assert payload["truncation"]["omitted_items"] >= 0


def test_rag_context_recent_brief_does_not_outrank_strong_long_term_hit() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        ctx = make_context(root)
        write_jsonl(
            ctx.kb_path,
            [
                {
                    "id": "strong-long-term",
                    "ts": "2026-01-01T00:00:00+00:00",
                    "kind": "issue",
                    "repo": "demo",
                    "branch": "main",
                    "title": "dynamic datasource primary missing",
                    "story": "stable fix for datasource primary",
                    "trigger_terms": ["dynamic-datasource", "primary"],
                    "source_paths": ["logs/app.log"],
                }
            ],
        )
        write_jsonl(
            root / "repos" / "_meta" / "session_briefs.jsonl",
            [
                kb_session_brief.build_brief(
                    title="recent note",
                    summary="user corrected current task boundary",
                    repo="demo",
                    branch="main",
                    cwd=str(root / "workspace"),
                    tags=["recent-session", "personal-kb"],
                    anchors=["primary"],
                    queries=["primary"],
                    used_entry_ids=[],
                    written_entry_ids=[],
                    updated_entry_ids=[],
                    source="codex",
                    session_id="s-1",
                )
            ],
        )

        stdout = io.StringIO()
        with (
            patch.object(kb_search, "resolve_context", return_value=ctx),
            patch.object(kb_search, "global_bucket_dir", return_value=root / "global"),
            patch.object(kb_search, "InvertedIndex", None),
            patch.object(kb_search, "get_inverted_index_path", None),
            patch.object(kb_session_brief, "resolve_context", return_value=ctx),
            patch.object(kb_session_brief, "kb_base_dir", return_value=root / "repos"),
            contextlib.redirect_stdout(stdout),
        ):
            rc = call_main(
                kb_rag_context.main,
                ["dynamic-datasource primary", "--json", "--limit", "5"],
            )

        assert rc == 0
        payload = json.loads(stdout.getvalue())
        assert [item["entry_id"] for item in payload["items"][:2]] == ["strong-long-term", payload["items"][1]["entry_id"]]
        assert payload["items"][1]["kind"] == "session_brief"


def test_rag_context_downgrades_personal_kb_design_noise_for_non_kb_query() -> None:
    ctx = make_context(Path("/tmp/demo"))
    raw_results = [
        {
            "id": "kb-design",
            "kind": "implementation",
            "repo": "demo",
            "branch": "main",
            "title": "timeout design note",
            "story": "runtime timeout design",
            "tags": ["personal-kb"],
            "trigger_terms": ["timeout"],
            "source_paths": ["skills/personal-kb/SKILL.md"],
        },
        {
            "id": "business-hit",
            "kind": "issue",
            "repo": "demo",
            "branch": "main",
            "title": "order timeout",
            "story": "business order timeout",
            "trigger_terms": ["timeout"],
            "source_paths": ["services/order/log.txt"],
        },
    ]

    stdout = io.StringIO()
    with (
        patch.object(kb_session_brief, "search_recent_briefs", return_value=[]),
        patch.object(kb_search, "_search_once", return_value=(raw_results, ctx)),
        patch.object(kb_search, "_format_cross_project_entry", side_effect=lambda entry, _repo: dict(entry)),
        contextlib.redirect_stdout(stdout),
    ):
        rc = call_main(
            kb_rag_context.main,
            ["timeout", "--json", "--no-plan", "--no-session-briefs", "--limit", "2"],
        )

    assert rc == 0
    payload = json.loads(stdout.getvalue())
    assert [item["entry_id"] for item in payload["items"]] == ["business-hit", "kb-design"]


def test_rag_context_loads_only_portable_configured_intents() -> None:
    assert [item["name"] for item in kb_rag_context._load_query_intents()] == ["retrieval-expansion"]

    ordinary = kb_rag_context._plan_query_groups("project-alpha 当前部署文档")
    configured_names = {item["name"] for item in kb_rag_context._load_query_intents()}
    assert all(plan.name not in configured_names for plan in ordinary)


def test_rag_context_retrieval_expansion_keeps_hard_anchor() -> None:
    expansion = "刚才零命中，请扩大范围再查以前的 component-x 启动故障"
    expansion_plans = kb_rag_context._plan_query_groups(expansion)
    assert any(plan.name == "retrieval-expansion" for plan in expansion_plans)
    assert "component-x" in next(
        plan.query for plan in expansion_plans if plan.name == "retrieval-expansion"
    )


def test_rag_context_precise_artifact_query_uses_current_anchor() -> None:
    ctx = make_context(Path("/tmp/demo"))
    raw_results = [
        {
            "id": "component-authority",
            "kind": "map",
            "repo": "demo",
            "branch": "main",
            "title": "component-x 当前版本权威入口",
            "tags": ["component-x", "配置", "权威版本"],
            "trigger_terms": ["component-x"],
            "source_paths": ["docs/component-x-current.md"],
            "artifact_locator": True,
        },
        {
            "id": "generic-document-noise",
            "kind": "map",
            "repo": "demo",
            "branch": "main",
            "title": "通用项目文档",
            "tags": ["项目", "文档"],
            "trigger_terms": ["文档"],
            "artifact_locator": True,
        },
    ]
    stdout = io.StringIO()
    with (
        patch.object(kb_session_brief, "search_recent_briefs", return_value=[]),
        patch.object(kb_search, "_search_once", return_value=(raw_results, ctx)),
        patch.object(kb_search, "_format_cross_project_entry", side_effect=lambda entry, _repo: dict(entry)),
        contextlib.redirect_stdout(stdout),
    ):
        rc = call_main(
            kb_rag_context.main,
            ["定位 component-x 当前版本文档", "--json", "--no-session-briefs"],
        )

    assert rc == 0
    payload = json.loads(stdout.getvalue())
    assert [item["entry_id"] for item in payload["items"]] == ["component-authority"]


def test_rag_context_synonym_anchor_survives_original_gate() -> None:
    ctx = make_context(Path("/tmp/demo"))
    raw_results = [
        {
            "id": "mysql-connection",
            "kind": "issue",
            "repo": "demo",
            "branch": "main",
            "title": "MySQL connection pool failure",
            "trigger_terms": ["mysql"],
            "source_paths": ["logs/mysql-startup.log"],
        },
        {
            "id": "generic-database-noise",
            "kind": "experience",
            "repo": "demo",
            "branch": "main",
            "title": "Java 后端知识整理",
            "story": "generic database prose without a reusable anchor",
            "tags": ["study", "项目"],
        },
    ]
    stdout = io.StringIO()
    with (
        patch.object(kb_rag_context, "load_config", return_value={"search": {"expand_queries": True}}),
        patch.object(kb_rag_context, "load_synonyms", return_value={"数据库": ["mysql"]}),
        patch.object(kb_session_brief, "search_recent_briefs", return_value=[]),
        patch.object(kb_search, "_search_once", return_value=(raw_results, ctx)),
        patch.object(kb_search, "_format_cross_project_entry", side_effect=lambda entry, _repo: dict(entry)),
        contextlib.redirect_stdout(stdout),
    ):
        rc = call_main(
            kb_rag_context.main,
            ["数据库", "--json", "--no-plan", "--no-session-briefs"],
        )

    assert rc == 0
    assert [item["entry_id"] for item in json.loads(stdout.getvalue())["items"]] == ["mysql-connection"]


def test_rag_context_does_not_treat_all_skill_updates_as_personal_kb() -> None:
    query = "更新所有 skill 和 MCP 的说明文件"
    assert kb_rag_context._is_kb_runtime_query(query) is False
    assert kb_rag_context._is_non_kb_maintenance_query(query) is True
    assert all(plan.name != "personal-kb" for plan in kb_rag_context._plan_query_groups(query))


def test_rag_context_rejects_nonactionable_cross_project_mixed_hit() -> None:
    ctx = make_context(Path("/tmp/demo"))
    raw_results = [
        {
            "id": "mixed-empty",
            "kind": "implementation",
            "repo": "other",
            "branch": "main",
            "title": "service timeout design",
            "story": "project-private detail",
            "trigger_terms": ["timeout"],
        }
    ]
    stdout = io.StringIO()
    with (
        patch.object(kb_session_brief, "search_recent_briefs", return_value=[]),
        patch.object(kb_search, "_search_once", return_value=(raw_results, ctx)),
        patch.object(kb_search, "_format_cross_project_entry", side_effect=lambda entry, _repo: {
            "id": entry["id"],
            "kind": entry["kind"],
            "repo": entry["repo"],
            "branch": entry["branch"],
            "title": entry["title"],
            "_cross_project": True,
            "_from_project": entry["repo"],
        }),
        contextlib.redirect_stdout(stdout),
    ):
        rc = call_main(kb_rag_context.main, ["timeout", "--json", "--global", "--no-session-briefs"])
    assert rc == 0
    payload = json.loads(stdout.getvalue())
    assert payload["items"] == []
    assert payload["rejected_nonactionable_count"] >= 1


def test_rag_context_cross_project_map_has_coordinate_summary() -> None:
    raw = {
        "id": "map-empty",
        "kind": "map",
        "repo": "other",
        "branch": "main",
        "title": "project coordinate",
    }
    item = kb_rag_context._compact_entry(
        raw,
        {**raw, "_cross_project": True, "_from_project": "other"},
        terms=["project"],
        max_snippet_chars=200,
    )
    assert "定位映射" in item["summary"]
    assert "other@main" in item["summary"]


def test_rag_context_rejects_generic_artifact_map_without_concrete_anchor() -> None:
    ctx = make_context(Path("/tmp/demo"))
    raw_results = [
        {
            "id": "generic-artifact",
            "kind": "map",
            "repo": "study",
            "branch": "main",
            "title": "project-alpha release material",
            "story": "generic historical project material",
            "tags": ["study", "recent-session", "项目", "历史"],
            "artifact_locator": True,
        }
    ]
    stdout = io.StringIO()
    with (
        patch.object(kb_session_brief, "search_recent_briefs", return_value=[]),
        patch.object(kb_search, "_search_once", return_value=(raw_results, ctx)),
        patch.object(kb_search, "_format_cross_project_entry", side_effect=lambda entry, _repo: dict(entry)),
        contextlib.redirect_stdout(stdout),
    ):
        rc = call_main(
            kb_rag_context.main,
            ["study recent-session 历史 项目", "--json", "--no-session-briefs"],
        )
    assert rc == 0
    assert json.loads(stdout.getvalue())["items"] == []


def test_rag_context_fault_query_prioritizes_pitfall_over_artifact_map() -> None:
    ctx = make_context(Path("/tmp/demo"))
    raw_results = [
        {
            "id": "shell-artifact",
            "kind": "map",
            "repo": "demo",
            "branch": "main",
            "title": "desktop-shell troubleshooting document",
            "tags": ["desktop-shell"],
            "trigger_terms": ["desktop-shell"],
            "artifact_locator": True,
        },
        {
            "id": "shell-pitfall",
            "kind": "pitfall",
            "repo": "demo",
            "branch": "main",
            "title": "desktop-shell startup freeze",
            "story": "verified root cause and recovery",
            "trigger_terms": ["desktop-shell", "卡死"],
        },
    ]
    stdout = io.StringIO()
    with (
        patch.object(kb_session_brief, "search_recent_briefs", return_value=[]),
        patch.object(kb_search, "_search_once", return_value=(raw_results, ctx)),
        patch.object(kb_search, "_format_cross_project_entry", side_effect=lambda entry, _repo: dict(entry)),
        contextlib.redirect_stdout(stdout),
    ):
        rc = call_main(
            kb_rag_context.main,
            ["desktop-shell 卡死 问题", "--json", "--no-session-briefs", "--limit", "2"],
        )
    assert rc == 0
    assert json.loads(stdout.getvalue())["items"][0]["entry_id"] == "shell-pitfall"


def test_rag_context_rejects_session_brief_matched_only_by_generic_context() -> None:
    ctx = make_context(Path("/tmp/demo"))
    generic_brief = {
        "entry_id": "brief-generic",
        "kind": "session_brief",
        "title": "recent note",
        "repo": "study",
        "branch": "main",
        "confidence": 0.8,
        "matched_fields": ["tags", "repo"],
        "summary": "unrelated recent work",
        "anchors": ["recent-session"],
        "queries": [],
        "_score": 3.0,
    }
    stdout = io.StringIO()
    with (
        patch.object(kb_session_brief, "search_recent_briefs", return_value=[generic_brief]),
        patch.object(kb_search, "_search_once", return_value=([], ctx)),
        contextlib.redirect_stdout(stdout),
    ):
        rc = call_main(kb_rag_context.main, ["study recent-session", "--json"])
    assert rc == 0
    assert json.loads(stdout.getvalue())["items"] == []


def test_rag_context_boosts_prior_requirement_but_keeps_recheck_warning() -> None:
    ctx = make_context(Path("/tmp/demo"))
    raw_results = [
        {
            "id": "export-decision",
            "kind": "requirement",
            "repo": "demo",
            "branch": "main",
            "title": "export-format 已确认",
            "story": "批处理导出继续使用 CSV 格式。",
            "tags": ["export-format", "CSV", "格式"],
            "trigger_terms": ["export-format", "CSV"],
            "status": "decision_confirmed",
        }
    ]
    stdout = io.StringIO()
    with (
        patch.object(kb_session_brief, "search_recent_briefs", return_value=[]),
        patch.object(kb_search, "_search_once", return_value=(raw_results, ctx)),
        patch.object(kb_search, "_format_cross_project_entry", side_effect=lambda entry, _repo: dict(entry)),
        contextlib.redirect_stdout(stdout),
    ):
        rc = call_main(
            kb_rag_context.main,
            ["export-format 是否保留 CSV，按上次决定", "--json", "--no-session-briefs"],
        )
    assert rc == 0
    payload = json.loads(stdout.getvalue())
    assert payload["items"][0]["entry_id"] == "export-decision"
    assert "verify against the current request and current evidence" in payload["items"][0]["warning"]


def test_rag_context_recognizes_natural_prior_decision_phrasing() -> None:
    export_format = "export-format 是否保留 CSV？按上次确认的决定来。"
    deployment_variant = "deployment-policy 备选版本，之前的那个呢？前两天的也保留。"

    assert kb_rag_context._is_prior_decision_query(export_format)
    assert kb_rag_context._is_prior_decision_query(deployment_variant)
    export_plans = kb_rag_context._plan_query_groups(export_format)
    deployment_plans = kb_rag_context._plan_query_groups(deployment_variant)
    assert export_plans[0].name == "prior-decision"
    assert "export-format" in export_plans[0].query
    assert deployment_plans[0].name == "prior-decision"
    assert "deployment-policy" in deployment_plans[0].query
    english_plans = kb_rag_context._plan_query_groups(
        "Use the decision we agreed on last time for component_x."
    )
    assert english_plans[0].name == "prior-decision"
    assert any(
        "component_x" in plan.query
        for plan in english_plans
        if plan.name in {"prior-decision", "hard-anchors", "specific-terms"}
    )
    assert not kb_rag_context._is_prior_decision_query("前两天这个 500 又发生了，请查相似 issue")


def test_rag_context_prior_decision_requires_confirmed_status_or_authority() -> None:
    ctx = make_context(Path("/tmp/demo"))
    raw_results = [
        {
            "id": "authority-confirmed",
            "kind": "requirement",
            "repo": "demo",
            "branch": "main",
            "title": "export-format 权威口径",
            "story": "批处理导出继续使用 CSV 格式。",
            "tags": ["export-format", "CSV", "格式"],
            "trigger_terms": ["export-format", "CSV"],
            "status": "current",
            "authority": "verified_summary",
        },
        {
            "id": "plain-requirement",
            "kind": "requirement",
            "repo": "demo",
            "branch": "main",
            "title": "export-format 普通建议",
            "story": "批处理导出也许可以改用 JSON。",
            "tags": ["export-format", "JSON", "格式"],
            "trigger_terms": ["export-format", "JSON"],
            "status": "current",
        },
    ]
    stdout = io.StringIO()
    with (
        patch.object(kb_session_brief, "search_recent_briefs", return_value=[]),
        patch.object(kb_search, "_search_once", return_value=(raw_results, ctx)),
        patch.object(kb_search, "_format_cross_project_entry", side_effect=lambda entry, _repo: dict(entry)),
        contextlib.redirect_stdout(stdout),
    ):
        rc = call_main(
            kb_rag_context.main,
            ["export-format 是否保留 CSV，按上次决定", "--json", "--no-session-briefs"],
        )

    assert rc == 0
    payload = json.loads(stdout.getvalue())
    assert [item["entry_id"] for item in payload["items"]] == ["authority-confirmed"]
    assert "verify against the current request and current evidence" in payload["items"][0]["warning"]


def test_rag_context_keeps_precise_artifact_lookup() -> None:
    ctx = make_context(Path("/tmp/demo"))
    raw_results = [
        {
            "id": "orion-artifact",
            "kind": "map",
            "repo": "demo",
            "branch": "main",
            "title": "orion-service Java 后端迁移文档",
            "tags": ["orion-service", "迁移", "文档"],
            "trigger_terms": ["orion-service"],
            "source_paths": ["docs/orion-service-migration.md"],
            "artifact_locator": True,
        }
    ]
    stdout = io.StringIO()
    with (
        patch.object(kb_session_brief, "search_recent_briefs", return_value=[]),
        patch.object(kb_search, "_search_once", return_value=(raw_results, ctx)),
        patch.object(kb_search, "_format_cross_project_entry", side_effect=lambda entry, _repo: dict(entry)),
        contextlib.redirect_stdout(stdout),
    ):
        rc = call_main(
            kb_rag_context.main,
            ["orion-service Java 后端 迁移文档", "--json", "--no-session-briefs"],
        )
    assert rc == 0
    assert json.loads(stdout.getvalue())["items"][0]["entry_id"] == "orion-artifact"


def test_rag_context_rejects_nonmap_without_original_concrete_anchor() -> None:
    ctx = make_context(Path("/tmp/demo"))
    raw_results = [
        {
            "id": "unrelated-requirement",
            "kind": "requirement",
            "repo": "study",
            "branch": "main",
            "title": "退出 desktop-shell 前安排延迟重启",
            "story": "Skill updater must restart safely.",
            "trigger_terms": ["desktop-shell", "skill"],
        }
    ]
    stdout = io.StringIO()
    with (
        patch.object(kb_session_brief, "search_recent_briefs", return_value=[]),
        patch.object(kb_search, "_search_once", return_value=(raw_results, ctx)),
        patch.object(kb_search, "_format_cross_project_entry", side_effect=lambda entry, _repo: dict(entry)),
        contextlib.redirect_stdout(stdout),
    ):
        rc = call_main(
            kb_rag_context.main,
            ["plugin-governance 历史误触发 流程膨胀 skill 插件", "--json", "--no-session-briefs"],
        )
    assert rc == 0
    assert json.loads(stdout.getvalue())["items"] == []


def test_rag_context_fault_query_requires_product_and_fault_anchor() -> None:
    ctx = make_context(Path("/tmp/demo"))
    raw_results = [
        {
            "id": "restart-only",
            "kind": "requirement",
            "repo": "demo",
            "branch": "main",
            "title": "desktop-shell safe restart",
            "trigger_terms": ["desktop-shell"],
        },
        {
            "id": "ime-pitfall",
            "kind": "pitfall",
            "repo": "demo",
            "branch": "main",
            "title": "desktop-shell WSLg input method failure",
            "trigger_terms": ["desktop-shell", "WSLg", "IBus"],
        },
        {
            "id": "time-false-positive",
            "kind": "pitfall",
            "repo": "demo",
            "branch": "main",
            "title": "unrelated timeout note",
            "trigger_terms": ["runtime"],
        },
    ]
    stdout = io.StringIO()
    with (
        patch.object(kb_session_brief, "search_recent_briefs", return_value=[]),
        patch.object(kb_search, "_search_once", return_value=(raw_results, ctx)),
        patch.object(kb_search, "_format_cross_project_entry", side_effect=lambda entry, _repo: dict(entry)),
        contextlib.redirect_stdout(stdout),
    ):
        rc = call_main(
            kb_rag_context.main,
            ["desktop-shell WSLg IME IBus 无法输入中文", "--json", "--no-session-briefs"],
        )
    assert rc == 0
    assert [item["entry_id"] for item in json.loads(stdout.getvalue())["items"]] == ["ime-pitfall"]


def main() -> int:
    tests = [
        test_search_matches_trigger_terms_and_source_paths,
        test_rag_context_is_compact_and_read_only,
        test_rag_context_rejects_invalid_retrieval_id,
        test_rag_context_surfaces_prior_outcome_feedback_without_cross_project_paths,
        test_rag_context_persists_strict_receipt_and_atomic_output_before_stdout,
        test_retrieval_receipt_retry_is_idempotent_and_conflict_is_rejected,
        test_retrieval_receipt_rejects_unrelated_query_with_fake_scope_anchors,
        test_rag_context_cli_rejects_unbound_scope_anchor_before_stdout,
        test_retrieval_receipt_accepts_unicode_path_and_hyphen_scope_bindings,
        test_scope_anchor_binding_rejects_ascii_identifier_prefix_match,
        test_receipt_output_conflict_rejects_before_log_append,
        test_markdown_header_escapes_query_control_tokens,
        test_rag_context_redacts_cross_project_paths,
        test_rag_context_ranks_authoritative_record_above_superseded_memory_design,
        test_rag_context_filters_noncurrent_records_by_default,
        test_rag_context_plans_task_query_and_filters_weak_story_noise,
        test_rag_context_recognizes_unspaced_chinese_kbskill_query,
        test_rag_context_accepts_planner_only_concept_match_but_rejects_story_noise,
        test_rag_context_keeps_precise_project_hits_after_weak_filtering,
        test_rag_context_reads_recent_session_briefs_before_long_term_kb,
        test_rag_context_filters_by_min_confidence,
        test_rag_context_applies_max_total_chars_stably,
        test_rag_context_recent_brief_does_not_outrank_strong_long_term_hit,
        test_rag_context_downgrades_personal_kb_design_noise_for_non_kb_query,
        test_rag_context_loads_only_portable_configured_intents,
        test_rag_context_retrieval_expansion_keeps_hard_anchor,
        test_rag_context_precise_artifact_query_uses_current_anchor,
        test_rag_context_synonym_anchor_survives_original_gate,
        test_rag_context_does_not_treat_all_skill_updates_as_personal_kb,
        test_rag_context_rejects_nonactionable_cross_project_mixed_hit,
        test_rag_context_cross_project_map_has_coordinate_summary,
        test_rag_context_rejects_generic_artifact_map_without_concrete_anchor,
        test_rag_context_fault_query_prioritizes_pitfall_over_artifact_map,
        test_rag_context_rejects_session_brief_matched_only_by_generic_context,
        test_rag_context_boosts_prior_requirement_but_keeps_recheck_warning,
        test_rag_context_recognizes_natural_prior_decision_phrasing,
        test_rag_context_prior_decision_requires_confirmed_status_or_authority,
        test_rag_context_keeps_precise_artifact_lookup,
        test_rag_context_rejects_nonmap_without_original_concrete_anchor,
        test_rag_context_fault_query_requires_product_and_fault_anchor,
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

    print("kb_rag_context tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
