#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

_ORIGINAL_PLATFORM = sys.platform
sys.platform = "test"
try:
    import kb_record_codex_effectiveness
finally:
    sys.platform = _ORIGINAL_PLATFORM


CURRENT_SHELL_FIXTURE = (
    Path(__file__).with_name("fixtures") / "codex_custom_exec_shell_command.jsonl"
)


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _function_call(call_id: str, cmd: str) -> dict:
    return {
        "type": "response_item",
        "payload": {
            "type": "function_call",
            "name": "exec_command",
            "call_id": call_id,
            "arguments": json.dumps({"cmd": cmd}, ensure_ascii=False),
        },
    }


def _function_output(call_id: str, output: str) -> dict:
    return {
        "type": "response_item",
        "payload": {
            "type": "function_call_output",
            "call_id": call_id,
            "output": output,
        },
    }


def _closeout_output(call_id: str, payload: dict, *, exit_code: int = 0) -> dict:
    return _function_output(
        call_id,
        f"Process exited with code {exit_code}\nOutput:\n{json.dumps(payload, ensure_ascii=False)}\n",
    )


def _custom_exec_call(call_id: str, cmd: str) -> dict:
    source = f"const r = await tools.exec_command({json.dumps({'cmd': cmd}, ensure_ascii=False)}); text(r.output);"
    return {
        "type": "response_item",
        "payload": {
            "type": "custom_tool_call",
            "name": "exec",
            "call_id": call_id,
            "input": source,
            "status": "completed",
        },
    }


def _custom_exec_output(call_id: str, text: str) -> dict:
    return {
        "type": "response_item",
        "payload": {
            "type": "custom_tool_call_output",
            "call_id": call_id,
            "output": [{"type": "input_text", "text": text}],
        },
    }


def _subagent_source(parent_thread_id: str = "parent-1", role: str = "explorer") -> dict:
    return {
        "subagent": {
            "thread_spawn": {
                "parent_thread_id": parent_thread_id,
                "depth": 1,
                "agent_path": None,
                "agent_nickname": "Worker",
                "agent_role": role,
            }
        }
    }


def test_record_codex_effectiveness_writes_searchable_log_and_summary() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        sessions_root = root / "sessions"
        kb_root = root / "repos"

        session_one = sessions_root / "2026" / "07" / "03" / "session-one.jsonl"
        write_jsonl(
            session_one,
            [
                {
                    "type": "session_meta",
                    "payload": {
                        "id": "s-1",
                        "timestamp": "2026-07-03T10:00:00+08:00",
                        "cwd": "/repo/demo",
                        "thread_source": "user",
                    },
                },
                {
                    "type": "event_msg",
                    "payload": {
                        "type": "user_message",
                        "message": "继续优化 personal-kb closeout，分析历史效果",
                    },
                },
                _function_call(
                    "call-rag",
                    "python3 /home/tester/.local/skills/personal-kb/scripts/kb_rag_context.py \"personal-kb closeout\" --limit 5",
                ),
                _function_output(
                    "call-rag",
                    "Chunk ID: x\nProcess exited with code 0\nOutput:\nKB_RAG_CONTEXT query=\"personal-kb closeout\" hits=2\n",
                ),
                _function_call(
                    "call-closeout",
                    "python3 /home/tester/.local/skills/personal-kb/scripts/kb_closeout.py --query \"personal-kb closeout\" --hit-count 2 --rag-calls 1 --used entry-1 --reason \"used historical kb\"",
                ),
                _closeout_output(
                    "call-closeout",
                    {
                        "event": "kb_closeout",
                        "status": "ok",
                        "queries": ["personal-kb closeout"],
                        "rag_calls": 1,
                        "hit_count": 2,
                        "used_entry_ids": ["entry-1"],
                        "adoption_effects": {"locate": [], "decide": [], "fix": [], "write": []},
                        "adopted_entry_ids": ["entry-1"],
                    },
                ),
            ],
        )

        session_two = sessions_root / "2026" / "07" / "03" / "session-two.jsonl"
        write_jsonl(
            session_two,
            [
                {
                    "type": "session_meta",
                    "payload": {
                        "id": "s-2",
                        "timestamp": "2026-07-03T11:00:00+08:00",
                        "cwd": "/repo/demo",
                        "thread_source": "user",
                    },
                },
                {
                    "type": "event_msg",
                    "payload": {
                        "type": "user_message",
                        "message": "继续上次项目分析，结合之前历史材料整理一下",
                    },
                },
            ],
        )

        result = kb_record_codex_effectiveness.rebuild_logs(
            sessions_root=sessions_root,
            base_dir=kb_root,
            force=True,
        )

        assert result["session_total"] == 2
        assert result["legacy_excluded_rows"] == 0
        rows = read_jsonl(kb_record_codex_effectiveness.log_path(kb_root))
        assert len(rows) == 2

        effective = next(row for row in rows if row["session_id"] == "s-1")
        missed = next(row for row in rows if row["session_id"] == "s-2")

        assert effective["effect_verdict"] == "used_hit_adopted"
        assert effective["kb_expected"] is True
        assert effective["kb_used"] is True
        assert effective["closeout_hit_count_total"] == 2
        assert effective["closeout_used_count_total"] == 1
        assert effective["closeout_adopted_count_total"] == 1
        assert effective["legacy_used_entry_ids"] == ["entry-1"]
        assert effective["adopted_entry_ids"] == ["entry-1"]
        assert effective["adopted_count"] == 1
        assert effective["adoption_effects"] == {"locate": [], "decide": [], "fix": [], "write": []}

        assert missed["effect_verdict"] == "needed_but_not_used"
        assert missed["kb_expected"] is True
        assert missed["kb_used"] is False

        summary = json.loads(kb_record_codex_effectiveness.summary_path(kb_root).read_text(encoding="utf-8"))
        assert summary["session_total"] == 2
        assert summary["main_session_total"] == 2
        assert summary["subagent_session_total"] == 0
        assert summary["kb_expected_sessions"] == 2
        assert summary["kb_used_sessions"] == 1
        assert summary["kb_expected_and_used_sessions"] == 1
        assert summary["kb_usage_rate"] == 0.5
        assert summary["effective_sessions"] == 1
        assert summary["adopted_sessions"] == 1
        assert summary["adopted_count"] == 1
        assert summary["legacy_used_count"] == 1
        assert summary["missed_sessions"] == 1
        assert summary["summary_scope"] == "current"
        assert summary["dedupe_session_id"] is True


def test_subagent_history_task_does_not_count_as_main_missed_kb() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        sessions_root = root / "sessions"
        kb_root = root / "repos"

        main_session = sessions_root / "2026" / "07" / "03" / "main.jsonl"
        write_jsonl(
            main_session,
            [
                {
                    "type": "session_meta",
                    "payload": {
                        "id": "main-missed",
                        "timestamp": "2026-07-03T12:00:00+08:00",
                        "cwd": "/repo/demo",
                        "source": "cli",
                        "thread_source": "user",
                    },
                },
                {
                    "type": "event_msg",
                    "payload": {
                        "type": "user_message",
                        "message": "继续上次项目分析，结合之前历史材料整理一下",
                    },
                },
            ],
        )

        subagent_session = sessions_root / "2026" / "07" / "03" / "subagent.jsonl"
        write_jsonl(
            subagent_session,
            [
                {
                    "type": "session_meta",
                    "payload": {
                        "id": "subagent-history",
                        "timestamp": "2026-07-03T12:01:00+08:00",
                        "cwd": "/repo/demo",
                        "source": _subagent_source(),
                        "thread_source": "subagent",
                    },
                },
                {
                    "type": "event_msg",
                    "payload": {
                        "type": "user_message",
                        "message": "你负责只读探索。继续检查历史问题和项目分析，不改文件，返回关键路径。",
                    },
                },
                {
                    "type": "response_item",
                    "payload": {
                        "type": "message",
                        "role": "developer",
                        "content": [{"type": "input_text", "text": "Authorization: ordinary worker, not a KB scout."}],
                    },
                },
            ],
        )

        kb_record_codex_effectiveness.rebuild_logs(
            sessions_root=sessions_root,
            base_dir=kb_root,
            force=True,
        )

        rows = read_jsonl(kb_record_codex_effectiveness.log_path(kb_root))
        main = next(row for row in rows if row["session_id"] == "main-missed")
        subagent = next(row for row in rows if row["session_id"] == "subagent-history")

        assert main["effect_verdict"] == "needed_but_not_used"
        assert main["kb_expected"] is True
        assert main["is_subagent"] is False

        assert subagent["effect_verdict"] == "subagent_no_kb_expected"
        assert subagent["kb_expected"] is False
        assert subagent["is_subagent"] is True
        assert subagent["subagent_parent_thread_id"] == "parent-1"
        assert subagent["subagent_missing_no_kb_guard"] is True

        summary = json.loads(kb_record_codex_effectiveness.summary_path(kb_root).read_text(encoding="utf-8"))
        assert summary["session_total"] == 2
        assert summary["main_session_total"] == 1
        assert summary["subagent_session_total"] == 1
        assert summary["kb_expected_sessions"] == 1
        assert summary["missed_sessions"] == 1
        assert summary["subagent_no_kb_expected_sessions"] == 1
        assert summary["subagent_missing_no_kb_guard_sessions"] == 1


def test_subagent_forbidden_kb_usage_is_reported_separately() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        sessions_root = root / "sessions"
        kb_root = root / "repos"

        subagent_session = sessions_root / "2026" / "07" / "03" / "subagent-forbidden.jsonl"
        write_jsonl(
            subagent_session,
            [
                {
                    "type": "session_meta",
                    "payload": {
                        "id": "subagent-forbidden",
                        "timestamp": "2026-07-03T13:00:00+08:00",
                        "cwd": "/repo/demo",
                        "source": repr(_subagent_source(parent_thread_id="parent-2", role="worker")),
                    },
                },
                {
                    "type": "event_msg",
                    "payload": {
                        "type": "user_message",
                        "message": "你是子 agent，只读检查，不要调用 KB，不要运行 kb_* 脚本。",
                    },
                },
                _function_call(
                    "call-rag",
                    "python3 /home/tester/.local/skills/personal-kb/scripts/kb_rag_context.py \"demo\" --limit 5",
                ),
                _function_output(
                    "call-rag",
                    "Chunk ID: x\nProcess exited with code 0\nOutput:\nKB_RAG_CONTEXT query=\"demo\" hits=1\n",
                ),
            ],
        )

        kb_record_codex_effectiveness.rebuild_logs(
            sessions_root=sessions_root,
            base_dir=kb_root,
            force=True,
        )

        rows = read_jsonl(kb_record_codex_effectiveness.log_path(kb_root))
        row = rows[0]
        assert row["is_subagent"] is True
        assert row["forbid_kb"] is True
        assert row["kb_used"] is True
        assert row["effect_verdict"] == "subagent_forbidden_but_used"

        summary = json.loads(kb_record_codex_effectiveness.summary_path(kb_root).read_text(encoding="utf-8"))
        assert summary["main_session_total"] == 0
        assert summary["subagent_session_total"] == 1
        assert summary["kb_expected_sessions"] == 0
        assert summary["missed_sessions"] == 0
        assert summary["subagent_forbidden_but_used_sessions"] == 1


def test_subagent_unexpected_kb_usage_is_reported_separately() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        sessions_root = root / "sessions"
        kb_root = root / "repos"

        subagent_session = sessions_root / "2026" / "07" / "03" / "subagent-unexpected.jsonl"
        write_jsonl(
            subagent_session,
            [
                {
                    "type": "session_meta",
                    "payload": {
                        "id": "subagent-unexpected",
                        "timestamp": "2026-07-03T13:00:00+08:00",
                        "cwd": "/repo/demo",
                        "source": _subagent_source(parent_thread_id="parent-2", role="worker"),
                        "thread_source": "subagent",
                    },
                },
                {
                    "type": "event_msg",
                    "payload": {
                        "type": "user_message",
                        "message": "你是子 agent，只读分析项目代码，返回证据路径。",
                    },
                },
                {
                    "type": "response_item",
                    "payload": {
                        "type": "message",
                        "role": "developer",
                        "content": [{"type": "input_text", "text": "Authorization: ordinary worker, not a KB scout."}],
                    },
                },
                _function_call(
                    "call-rag",
                    "python3 /home/tester/.local/skills/personal-kb/scripts/kb_rag_context.py \"demo\" --limit 5",
                ),
                _function_output(
                    "call-rag",
                    "Chunk ID: x\nProcess exited with code 0\nOutput:\nKB_RAG_CONTEXT query=\"demo\" hits=1\n",
                ),
            ],
        )

        kb_record_codex_effectiveness.rebuild_logs(
            sessions_root=sessions_root,
            base_dir=kb_root,
            force=True,
        )

        row = read_jsonl(kb_record_codex_effectiveness.log_path(kb_root))[0]
        assert row["is_subagent"] is True
        assert row["kb_expected"] is False
        assert row["kb_used"] is True
        assert row["effect_verdict"] == "subagent_unexpected_kb_used"

        summary = json.loads(kb_record_codex_effectiveness.summary_path(kb_root).read_text(encoding="utf-8"))
        assert summary["main_session_total"] == 0
        assert summary["subagent_session_total"] == 1
        assert summary["subagent_unexpected_kb_used_sessions"] == 1
        assert summary["subagent_forbidden_but_used_sessions"] == 0


def test_quality_gate_fails_on_subagent_unexpected_kb_usage() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        sessions_root = root / "sessions"
        kb_root = root / "repos"

        subagent_session = sessions_root / "2026" / "07" / "03" / "subagent-unexpected.jsonl"
        write_jsonl(
            subagent_session,
            [
                {
                    "type": "session_meta",
                    "payload": {
                        "id": "subagent-unexpected",
                        "timestamp": "2026-07-03T13:00:00+08:00",
                        "cwd": "/repo/demo",
                        "source": _subagent_source(parent_thread_id="parent-2", role="worker"),
                        "thread_source": "subagent",
                    },
                },
                {
                    "type": "event_msg",
                    "payload": {
                        "type": "user_message",
                        "message": "你是子 agent，只读分析项目代码，返回证据路径。",
                    },
                },
                {
                    "type": "response_item",
                    "payload": {
                        "type": "message",
                        "role": "developer",
                        "content": [{"type": "input_text", "text": "Authorization: ordinary worker, not a KB scout."}],
                    },
                },
                _function_call(
                    "call-rag",
                    "python3 /home/tester/.local/skills/personal-kb/scripts/kb_rag_context.py \"demo\" --limit 5",
                ),
                _function_output("call-rag", "Process exited with code 0\nKB_RAG_CONTEXT query=\"demo\" hits=1\n"),
            ],
        )

        code = kb_record_codex_effectiveness.main(
            [
                "--sessions-root",
                str(sessions_root),
                "--root",
                str(kb_root),
                "--force",
                "--fail-on-subagent-unexpected-kb",
            ]
        )

        assert code == 2


def test_quality_gate_passes_when_subagent_has_no_kb_usage() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        sessions_root = root / "sessions"
        kb_root = root / "repos"

        subagent_session = sessions_root / "2026" / "07" / "03" / "subagent-clean.jsonl"
        write_jsonl(
            subagent_session,
            [
                {
                    "type": "session_meta",
                    "payload": {
                        "id": "subagent-clean",
                        "timestamp": "2026-07-03T13:00:00+08:00",
                        "cwd": "/repo/demo",
                        "source": _subagent_source(parent_thread_id="parent-2", role="worker"),
                        "thread_source": "subagent",
                    },
                },
                {
                    "type": "event_msg",
                    "payload": {
                        "type": "user_message",
                        "message": (
                            "你是子 agent，只读分析项目代码，返回证据路径。"
                            "Do not run personal-kb scripts. Use only KB hints provided by the parent; "
                            "the parent owns KB retrieval, closeout, heating, and writes."
                        ),
                    },
                },
            ],
        )

        code = kb_record_codex_effectiveness.main(
            [
                "--sessions-root",
                str(sessions_root),
                "--root",
                str(kb_root),
                "--force",
                "--strict-quality",
            ]
        )

        assert code == 0


def test_quality_gate_fails_on_missing_subagent_kb_guard() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        sessions_root = root / "sessions"
        kb_root = root / "repos"

        subagent_session = sessions_root / "2026" / "07" / "03" / "subagent-missing-guard.jsonl"
        write_jsonl(
            subagent_session,
            [
                {
                    "type": "session_meta",
                    "payload": {
                        "id": "subagent-missing-guard",
                        "timestamp": "2026-07-03T13:00:00+08:00",
                        "cwd": "/repo/demo",
                        "source": _subagent_source(parent_thread_id="parent-2", role="worker"),
                        "thread_source": "subagent",
                    },
                },
                {
                    "type": "event_msg",
                    "payload": {
                        "type": "user_message",
                        "message": "你是子 agent，只读分析项目代码，返回证据路径。",
                    },
                },
                {
                    "type": "response_item",
                    "payload": {
                        "type": "message",
                        "role": "developer",
                        "content": [{"type": "input_text", "text": "Authorization: ordinary worker, not a KB scout."}],
                    },
                },
            ],
        )

        code = kb_record_codex_effectiveness.main(
            [
                "--sessions-root",
                str(sessions_root),
                "--root",
                str(kb_root),
                "--force",
                "--fail-on-subagent-missing-kb-guard",
            ]
        )

        assert code == 2


def test_legacy_rows_are_archived_and_excluded_from_current_summary() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        sessions_root = root / "sessions"
        kb_root = root / "repos"

        legacy_session = sessions_root / "2026" / "04" / "01" / "legacy.jsonl"
        write_jsonl(
            legacy_session,
            [
                {
                    "type": "session_meta",
                    "payload": {
                        "id": "legacy-main",
                        "timestamp": "2026-04-01T10:00:00+08:00",
                        "cwd": "/repo/demo",
                        "thread_source": "user",
                    },
                },
                {
                    "type": "event_msg",
                    "payload": {"type": "user_message", "message": "继续之前的问题排查"},
                },
            ],
        )

        current_session = sessions_root / "2026" / "07" / "03" / "current.jsonl"
        write_jsonl(
            current_session,
            [
                {
                    "type": "session_meta",
                    "payload": {
                        "id": "current-main",
                        "timestamp": "2026-07-03T10:00:00+08:00",
                        "cwd": "/repo/demo",
                        "thread_source": "user",
                    },
                },
                {
                    "type": "event_msg",
                    "payload": {"type": "user_message", "message": "继续之前的问题排查"},
                },
            ],
        )

        result = kb_record_codex_effectiveness.rebuild_logs(
            sessions_root=sessions_root,
            base_dir=kb_root,
            force=True,
        )

        rows = read_jsonl(kb_record_codex_effectiveness.log_path(kb_root))
        legacy_rows = read_jsonl(kb_record_codex_effectiveness.legacy_log_path(kb_root))
        summary = json.loads(kb_record_codex_effectiveness.summary_path(kb_root).read_text(encoding="utf-8"))

        assert result["raw_session_file_total"] == 2
        assert result["session_total"] == 1
        assert result["legacy_excluded_rows"] == 1
        assert [row["session_id"] for row in rows] == ["current-main"]
        assert [row["session_id"] for row in legacy_rows] == ["legacy-main"]
        assert summary["session_total"] == 1
        assert summary["legacy_excluded_rows"] == 1
        assert summary["current_cutoff"] == kb_record_codex_effectiveness.CURRENT_WORKFLOW_CUTOFF


def test_duplicate_session_ids_are_collapsed_to_strongest_signal() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        sessions_root = root / "sessions"
        kb_root = root / "repos"

        weak_duplicate = sessions_root / "2026" / "07" / "03" / "dup-a.jsonl"
        write_jsonl(
            weak_duplicate,
            [
                {
                    "type": "session_meta",
                    "payload": {
                        "id": "dup-main",
                        "timestamp": "2026-07-03T10:00:00+08:00",
                        "cwd": "/repo/demo",
                        "thread_source": "user",
                    },
                },
                {
                    "type": "event_msg",
                    "payload": {"type": "user_message", "message": "继续之前的问题排查"},
                },
            ],
        )

        strong_duplicate = sessions_root / "2026" / "07" / "03" / "dup-b.jsonl"
        write_jsonl(
            strong_duplicate,
            [
                {
                    "type": "session_meta",
                    "payload": {
                        "id": "dup-main",
                        "timestamp": "2026-07-03T10:00:00+08:00",
                        "cwd": "/repo/demo",
                        "thread_source": "user",
                    },
                },
                {
                    "type": "event_msg",
                    "payload": {"type": "user_message", "message": "继续之前的问题排查"},
                },
                _function_call(
                    "call-rag",
                    "python3 /home/tester/.local/skills/personal-kb/scripts/kb_rag_context.py \"demo\" --limit 5",
                ),
                _function_output("call-rag", "Process exited with code 0\nKB_RAG_CONTEXT query=\"demo\" hits=1\n"),
                _function_call(
                    "call-closeout",
                    "python3 /home/tester/.local/skills/personal-kb/scripts/kb_closeout.py --query \"demo\" --hit-count 1 --rag-calls 1 --used entry-1 --reason ok",
                ),
                _closeout_output(
                    "call-closeout",
                    {
                        "event": "kb_closeout",
                        "status": "ok",
                        "queries": ["demo"],
                        "rag_calls": 1,
                        "hit_count": 1,
                        "used_entry_ids": ["entry-1"],
                        "adoption_effects": {"locate": [], "decide": [], "fix": [], "write": []},
                        "adopted_entry_ids": ["entry-1"],
                    },
                ),
            ],
        )

        result = kb_record_codex_effectiveness.rebuild_logs(
            sessions_root=sessions_root,
            base_dir=kb_root,
            force=True,
        )

        rows = read_jsonl(kb_record_codex_effectiveness.log_path(kb_root))
        summary = json.loads(kb_record_codex_effectiveness.summary_path(kb_root).read_text(encoding="utf-8"))

        assert result["raw_session_file_total"] == 2
        assert result["session_total"] == 1
        assert len(rows) == 1
        assert rows[0]["session_id"] == "dup-main"
        assert rows[0]["effect_verdict"] == "used_hit_adopted"
        assert rows[0]["dedupe_group_size"] == 2
        assert summary["duplicate_session_id_count"] == 1
        assert summary["duplicate_row_extra"] == 1
        assert summary["missed_sessions"] == 0
        assert summary["effective_sessions"] == 1


def test_rollout_uuid_selects_own_meta_from_inherited_multi_meta_session() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        sessions_root = root / "sessions"
        kb_root = root / "repos"
        parent_id = "019f5ee9-5ddf-76d1-ab86-e2a01e4c1b4b"
        child_id = "019f5f76-e9d7-7022-bccf-b8753b4e150a"

        parent_session = sessions_root / "2026" / "07" / "14" / f"rollout-2026-07-14T12-00-00-{parent_id}.jsonl"
        write_jsonl(
            parent_session,
            [
                {
                    "type": "session_meta",
                    "payload": {
                        "session_id": parent_id,
                        "id": parent_id,
                        "timestamp": "2026-07-14T12:00:00+08:00",
                        "cwd": "/repo/demo",
                        "source": "cli",
                        "thread_source": "user",
                    },
                },
                {
                    "type": "event_msg",
                    "payload": {"type": "user_message", "message": "检查当前文件即可"},
                },
            ],
        )

        child_session = sessions_root / "2026" / "07" / "14" / f"rollout-2026-07-14T12-01-00-{child_id}.jsonl"
        write_jsonl(
            child_session,
            [
                {
                    "type": "session_meta",
                    "payload": {
                        "session_id": parent_id,
                        "id": child_id,
                        "forked_from_id": parent_id,
                        "parent_thread_id": parent_id,
                        "timestamp": "2026-07-14T12:01:00+08:00",
                        "cwd": "/repo/demo",
                        "source": _subagent_source(parent_thread_id=parent_id, role="worker"),
                        "thread_source": "subagent",
                    },
                },
                # A forked rollout can contain the parent's copied session_meta after
                # its own metadata. The last metadata row must not win.
                {
                    "type": "session_meta",
                    "payload": {
                        "session_id": parent_id,
                        "id": parent_id,
                        "timestamp": "2026-07-14T12:00:00+08:00",
                        "cwd": "/repo/demo",
                        "source": "cli",
                        "thread_source": "user",
                    },
                },
                {
                    "type": "event_msg",
                    "payload": {
                        "type": "user_message",
                        "message": (
                            "只读检查当前文件。Do not run personal-kb scripts. "
                            "Use only KB hints provided by the parent; the parent owns KB retrieval, closeout, heating, and writes."
                        ),
                    },
                },
            ],
        )

        result = kb_record_codex_effectiveness.rebuild_logs(
            sessions_root=sessions_root,
            base_dir=kb_root,
            force=True,
        )

        rows = read_jsonl(kb_record_codex_effectiveness.log_path(kb_root))
        by_id = {row["session_id"]: row for row in rows}
        child = by_id[child_id]
        assert result["session_total"] == 2
        assert child["rollout_id"] == child_id
        assert child["session_meta_count"] == 2
        assert child["session_meta_selection"] == "rollout_filename_id"
        assert child["is_subagent"] is True
        assert child["subagent_parent_thread_id"] == parent_id
        assert child["thread_source"] == "subagent"


def test_dedupe_keeps_main_and_subagent_with_same_session_id_separate() -> None:
    rows = [
        {
            "session_id": "shared-parent-id",
            "session_path": "/sessions/main.jsonl",
            "session_file": "main.jsonl",
            "session_ts": "2026-07-14T12:00:00+08:00",
            "is_subagent": False,
            "effect_verdict": "no_kb_needed",
        },
        {
            "session_id": "shared-parent-id",
            "session_path": "/sessions/child.jsonl",
            "session_file": "child.jsonl",
            "session_ts": "2026-07-14T12:01:00+08:00",
            "is_subagent": True,
            "effect_verdict": "subagent_no_kb_expected",
        },
    ]

    deduped, stats = kb_record_codex_effectiveness._dedupe_rows_by_session_id(rows)

    assert len(deduped) == 2
    assert stats["logical_session_total"] == 2
    assert stats["duplicate_session_id_count"] == 0
    assert stats["duplicate_row_extra"] == 0
    assert stats["cross_agent_scope_session_id_count"] == 1


def test_subagent_project_message_guard_is_observed_without_polluting_user_excerpt() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        sessions_root = root / "sessions"
        kb_root = root / "repos"
        child_id = "44444444-4444-7444-8444-444444444444"
        session = sessions_root / "2026" / "07" / "15" / f"rollout-2026-07-15T12-00-00-{child_id}.jsonl"
        write_jsonl(
            session,
            [
                {
                    "type": "session_meta",
                    "payload": {
                        "id": child_id,
                        "thread_source": "subagent",
                        "parent_thread_id": "parent-id",
                        "source": _subagent_source(parent_thread_id="parent-id", role="worker"),
                        "timestamp": "2026-07-15T12:00:00+08:00",
                    },
                },
                {
                    "type": "event_msg",
                    "payload": {"type": "user_message", "message": "检查当前代码"},
                },
                {
                    "type": "response_item",
                    "payload": {
                        "type": "message",
                        "role": "user",
                        "content": [
                            {
                                "type": "input_text",
                                "text": (
                                    "Project policy: Do not run personal-kb scripts. Use only KB hints provided by "
                                    "the parent; the parent owns KB retrieval, closeout, heating, and writes."
                                ),
                            }
                        ],
                    },
                },
            ],
        )

        kb_record_codex_effectiveness.rebuild_logs(
            sessions_root=sessions_root,
            base_dir=kb_root,
            force=True,
        )

        row = read_jsonl(kb_record_codex_effectiveness.log_path(kb_root))[0]
        assert row["user_excerpt"] == "检查当前代码"
        assert row["subagent_no_kb_guard_observed"] is True
        assert row["subagent_missing_no_kb_guard"] is False


def test_four_adoption_effect_flags_and_legacy_used_are_aggregated() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        sessions_root = root / "sessions"
        kb_root = root / "repos"
        session_id = "019f63d0-1820-7122-b414-67a3ab2c8a94"
        session = sessions_root / "2026" / "07" / "15" / f"rollout-2026-07-15T11-26-49-{session_id}.jsonl"
        write_jsonl(
            session,
            [
                {
                    "type": "session_meta",
                    "payload": {
                        "id": session_id,
                        "timestamp": "2026-07-15T11:26:49+08:00",
                        "cwd": "/repo/demo",
                        "thread_source": "user",
                    },
                },
                {
                    "type": "event_msg",
                    "payload": {"type": "user_message", "message": "继续审计 personal-kb 历史采用效果"},
                },
                _function_call(
                    "call-rag",
                    "python3 /home/tester/.local/skills/personal-kb/scripts/kb_rag_context.py \"demo\" --limit 5",
                ),
                _function_output("call-rag", "Process exited with code 0\nKB_RAG_CONTEXT query=\"demo\" hits=5\n"),
                _function_call(
                    "call-closeout",
                    "python3 /home/tester/.local/skills/personal-kb/scripts/kb_closeout.py "
                    "--query demo --hit-count 5 --rag-calls 1 "
                    "--used-locate=locate-1 --used-decide decide-1 --used-fix fix-1 "
                    "--used-write=write-1 --used legacy-1",
                ),
                _closeout_output(
                    "call-closeout",
                    {
                        "event": "kb_closeout",
                        "status": "ok",
                        "queries": ["demo"],
                        "rag_calls": 1,
                        "hit_count": 5,
                        "used_entry_ids": ["legacy-1", "locate-1", "decide-1", "fix-1", "write-1"],
                        "adoption_effects": {
                            "locate": ["locate-1"],
                            "decide": ["decide-1"],
                            "fix": ["fix-1"],
                            "write": ["write-1"],
                        },
                        "adopted_entry_ids": ["legacy-1", "locate-1", "decide-1", "fix-1", "write-1"],
                    },
                ),
            ],
        )

        kb_record_codex_effectiveness.rebuild_logs(
            sessions_root=sessions_root,
            base_dir=kb_root,
            force=True,
        )

        row = read_jsonl(kb_record_codex_effectiveness.log_path(kb_root))[0]
        assert row["effect_verdict"] == "used_hit_adopted"
        assert row["adoption_effects"] == {
            "locate": ["locate-1"],
            "decide": ["decide-1"],
            "fix": ["fix-1"],
            "write": ["write-1"],
        }
        assert row["adoption_effect_counts"] == {"locate": 1, "decide": 1, "fix": 1, "write": 1}
        assert row["legacy_used_entry_ids"] == ["legacy-1"]
        assert row["adopted_entry_ids"] == ["legacy-1", "locate-1", "decide-1", "fix-1", "write-1"]
        assert row["adopted_count"] == 5
        assert row["closeout_adopted_count_total"] == 5
        assert row["closeout_used_count_total"] == 5

        summary = json.loads(kb_record_codex_effectiveness.summary_path(kb_root).read_text(encoding="utf-8"))
        assert summary["effective_sessions"] == 1
        assert summary["adopted_sessions"] == 1
        assert summary["adopted_count"] == 5
        assert summary["adoption_effects"] == {"locate": 1, "decide": 1, "fix": 1, "write": 1}
        assert summary["legacy_used_count"] == 1


def test_actual_closeout_output_excludes_failed_and_session_brief_from_long_term_adoption() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        sessions_root = root / "sessions"
        kb_root = root / "repos"
        session = sessions_root / "2026" / "07" / "15" / "partial-closeout.jsonl"
        write_jsonl(
            session,
            [
                {
                    "type": "session_meta",
                    "payload": {
                        "id": "partial-closeout",
                        "timestamp": "2026-07-15T16:00:00+08:00",
                        "cwd": "/repo/demo",
                        "thread_source": "user",
                    },
                },
                {
                    "type": "event_msg",
                    "payload": {"type": "user_message", "message": "继续审计 personal-kb 实际采用结果"},
                },
                _function_call(
                    "rag",
                    "python3 /tmp/kb_rag_context.py demo --limit 3",
                ),
                _function_output("rag", "Process exited with code 0\nKB_RAG_CONTEXT query=demo hits=3\n"),
                _function_call(
                    "closeout",
                    "python3 /tmp/kb_closeout.py --query demo --hit-count 3 --rag-calls 1 "
                    "--used-locate locate-1 --used-fix failed-1 --used-write brief-1",
                ),
                _closeout_output(
                    "closeout",
                    {
                        "event": "kb_closeout",
                        "status": "partial_failure",
                        "queries": ["demo"],
                        "rag_calls": 1,
                        "hit_count": 3,
                        "used_entry_ids": ["locate-1", "failed-1"],
                        "adoption_effects": {
                            "locate": ["locate-1"],
                            "decide": [],
                            "fix": ["failed-1"],
                            "write": ["brief-1"],
                        },
                        "adopted_entry_ids": ["locate-1"],
                        "session_brief_used_entry_ids": ["brief-1"],
                        "heat_failed_entry_ids": ["failed-1"],
                    },
                    exit_code=3,
                ),
            ],
        )

        kb_record_codex_effectiveness.rebuild_logs(
            sessions_root=sessions_root,
            base_dir=kb_root,
            force=True,
        )

        row = read_jsonl(kb_record_codex_effectiveness.log_path(kb_root))[0]
        assert row["effect_verdict"] == "used_hit_adopted"
        assert row["requested_entry_ids"] == ["locate-1", "failed-1", "brief-1"]
        assert row["adopted_entry_ids"] == ["locate-1"]
        assert row["adoption_effects"] == {
            "locate": ["locate-1"],
            "decide": [],
            "fix": [],
            "write": [],
        }
        assert row["session_brief_used_entry_ids"] == ["brief-1"]
        assert row["session_brief_used_count"] == 1
        assert row["closeout_output_json_count"] == 1
        assert row["unpaired_retrieval_call_count"] == 0


def test_silent_closeout_keeps_requested_adoption_unconfirmed() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        sessions_root = root / "sessions"
        kb_root = root / "repos"
        session = sessions_root / "2026" / "07" / "15" / "silent-closeout.jsonl"
        write_jsonl(
            session,
            [
                {
                    "type": "session_meta",
                    "payload": {
                        "id": "silent-closeout",
                        "timestamp": "2026-07-15T16:01:00+08:00",
                        "cwd": "/repo/demo",
                        "thread_source": "user",
                    },
                },
                {
                    "type": "event_msg",
                    "payload": {"type": "user_message", "message": "继续审计 personal-kb 静默 closeout"},
                },
                _function_call("rag", "python3 /tmp/kb_rag_context.py demo --limit 1"),
                _function_output("rag", "Process exited with code 0\nKB_RAG_CONTEXT query=demo hits=1\n"),
                _function_call(
                    "closeout",
                    "python3 /tmp/kb_closeout.py --query demo --hit-count 1 --rag-calls 1 --used-fix fix-1",
                ),
                _function_output("closeout", "Process exited with code 0\n"),
            ],
        )

        kb_record_codex_effectiveness.rebuild_logs(
            sessions_root=sessions_root,
            base_dir=kb_root,
            force=True,
        )

        row = read_jsonl(kb_record_codex_effectiveness.log_path(kb_root))[0]
        assert row["effect_verdict"] == "used_hit_adoption_unconfirmed"
        assert row["requested_entry_ids"] == ["fix-1"]
        assert row["requested_adoption_effects"]["fix"] == ["fix-1"]
        assert row["adopted_entry_ids"] == []
        assert row["adoption_unconfirmed"] is True
        assert row["closeout_command_fallback_count"] == 1
        summary = json.loads(kb_record_codex_effectiveness.summary_path(kb_root).read_text(encoding="utf-8"))
        assert summary["adopted_count"] == 0
        assert summary["requested_adoption_count"] == 1
        assert summary["adoption_unconfirmed_sessions"] == 1
        assert summary["effective_sessions"] == 0


def test_later_unclosed_retrieval_is_not_hidden_by_earlier_closeout() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        sessions_root = root / "sessions"
        kb_root = root / "repos"
        session = sessions_root / "2026" / "07" / "15" / "long-session.jsonl"
        write_jsonl(
            session,
            [
                {
                    "type": "session_meta",
                    "payload": {
                        "id": "long-session",
                        "timestamp": "2026-07-15T16:02:00+08:00",
                        "cwd": "/repo/demo",
                        "thread_source": "user",
                    },
                },
                {
                    "type": "event_msg",
                    "payload": {"type": "user_message", "message": "继续检查上次的 personal-kb 历史任务"},
                },
                _function_call("rag-1", "python3 /tmp/kb_rag_context.py first --limit 1"),
                _function_output("rag-1", "Process exited with code 0\nKB_RAG_CONTEXT query=first hits=1\n"),
                _function_call(
                    "closeout-1",
                    "python3 /tmp/kb_closeout.py --query first --hit-count 1 --rag-calls 1 --reason rejected",
                ),
                _function_output("closeout-1", "Process exited with code 0\n"),
                _function_call("rag-2", "python3 /tmp/kb_rag_context.py second --limit 1"),
                _function_output("rag-2", "Process exited with code 0\nKB_RAG_CONTEXT query=second hits=1\n"),
            ],
        )

        kb_record_codex_effectiveness.rebuild_logs(
            sessions_root=sessions_root,
            base_dir=kb_root,
            force=True,
        )

        row = read_jsonl(kb_record_codex_effectiveness.log_path(kb_root))[0]
        assert row["effect_verdict"] == "used_without_closeout"
        assert row["retrieval_call_count"] == 2
        assert row["paired_retrieval_call_count"] == 1
        assert row["unpaired_retrieval_call_count"] == 1
        assert row["unpaired_rag_queries"] == ["second"]
        summary = json.loads(kb_record_codex_effectiveness.summary_path(kb_root).read_text(encoding="utf-8"))
        assert summary["missing_closeout_sessions"] == 1
        assert summary["main_unpaired_retrieval_call_count"] == 1


def test_subagent_kb_usage_with_unavailable_child_task_is_authorization_unknown() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        sessions_root = root / "sessions"
        kb_root = root / "repos"
        session = sessions_root / "2026" / "07" / "15" / "unknown-child-task.jsonl"
        write_jsonl(
            session,
            [
                {
                    "type": "session_meta",
                    "payload": {
                        "id": "unknown-child-task",
                        "timestamp": "2026-07-15T16:03:00+08:00",
                        "cwd": "/repo/demo",
                        "source": _subagent_source(parent_thread_id="parent-unknown", role="worker"),
                        "thread_source": "subagent",
                    },
                },
                {
                    "type": "event_msg",
                    "payload": {"type": "user_message", "message": "父会话继承的问题文本，不代表子任务授权"},
                },
                _function_call("rag", "python3 /tmp/kb_rag_context.py demo --limit 1"),
                _function_output("rag", "Process exited with code 0\nKB_RAG_CONTEXT query=demo hits=1\n"),
            ],
        )

        kb_record_codex_effectiveness.rebuild_logs(
            sessions_root=sessions_root,
            base_dir=kb_root,
            force=True,
        )

        row = read_jsonl(kb_record_codex_effectiveness.log_path(kb_root))[0]
        assert row["subagent_kb_authorization"] == "unknown"
        assert row["effect_verdict"] == "subagent_kb_used_authorization_unknown"
        summary = json.loads(kb_record_codex_effectiveness.summary_path(kb_root).read_text(encoding="utf-8"))
        assert summary["subagent_unexpected_kb_used_sessions"] == 0
        assert summary["subagent_kb_used_authorization_unknown_sessions"] == 1


def test_runtime_audit_and_current_incident_do_not_require_long_term_rag() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        sessions_root = root / "sessions"
        kb_root = root / "repos"
        write_jsonl(
            sessions_root / "2026" / "07" / "15" / "runtime-audit.jsonl",
            [
                {
                    "type": "session_meta",
                    "payload": {
                        "id": "runtime-audit",
                        "timestamp": "2026-07-15T16:04:00+08:00",
                        "cwd": "/repo/demo",
                        "thread_source": "user",
                    },
                },
                {
                    "type": "event_msg",
                    "payload": {
                        "type": "user_message",
                        "message": "审计 Personal KB 两天运行效果，直接读取 session/runtime 日志",
                    },
                },
            ],
        )
        write_jsonl(
            sessions_root / "2026" / "07" / "15" / "current-incident.jsonl",
            [
                {
                    "type": "session_meta",
                    "payload": {
                        "id": "current-incident",
                        "timestamp": "2026-07-15T16:05:00+08:00",
                        "cwd": "/repo/demo",
                        "thread_source": "user",
                    },
                },
                {
                    "type": "event_msg",
                    "payload": {
                        "type": "user_message",
                        "message": "排查当前线上 500，现有异常栈和 traceId 已足够定位",
                    },
                },
            ],
        )

        kb_record_codex_effectiveness.rebuild_logs(
            sessions_root=sessions_root,
            base_dir=kb_root,
            force=True,
        )

        rows = {
            row["session_id"]: row
            for row in read_jsonl(kb_record_codex_effectiveness.log_path(kb_root))
        }
        assert rows["runtime-audit"]["runtime_audit"] is True
        assert rows["runtime-audit"]["kb_expected"] is False
        assert rows["runtime-audit"]["effect_verdict"] == "no_kb_needed"
        assert rows["current-incident"]["runtime_audit"] is False
        assert rows["current-incident"]["kb_expected"] is False
        assert rows["current-incident"]["effect_verdict"] == "no_kb_needed"


def test_help_only_kb_script_invocation_is_not_counted_as_kb_usage() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        sessions_root = root / "sessions"
        kb_root = root / "repos"
        write_jsonl(
            sessions_root / "2026" / "07" / "15" / "help-only.jsonl",
            [
                {
                    "type": "session_meta",
                    "payload": {
                        "id": "help-only",
                        "timestamp": "2026-07-15T16:06:00+08:00",
                        "cwd": "/repo/demo",
                        "thread_source": "user",
                    },
                },
                {
                    "type": "event_msg",
                    "payload": {"type": "user_message", "message": "查看当前脚本帮助"},
                },
                _function_call("help", "python3 /tmp/kb_search.py --help"),
                _function_output("help", "Process exited with code 0\nusage: kb_search.py\n"),
            ],
        )

        kb_record_codex_effectiveness.rebuild_logs(
            sessions_root=sessions_root,
            base_dir=kb_root,
            force=True,
        )

        row = read_jsonl(kb_record_codex_effectiveness.log_path(kb_root))[0]
        assert row["kb_used"] is False
        assert row["retrieval_used"] is False
        assert row["retrieval_call_count"] == 0
        assert row["detected_kb_call_count"] == 0
        assert row["effect_verdict"] == "no_kb_needed"


def test_generic_skill_word_no_longer_forces_kb_expected() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        sessions_root = root / "sessions"
        kb_root = root / "repos"

        session = sessions_root / "2026" / "07" / "03" / "skill.jsonl"
        write_jsonl(
            session,
            [
                {
                    "type": "session_meta",
                    "payload": {
                        "id": "skill-only",
                        "timestamp": "2026-07-03T10:00:00+08:00",
                        "cwd": "/repo/demo",
                        "thread_source": "user",
                    },
                },
                {
                    "type": "event_msg",
                    "payload": {"type": "user_message", "message": "cc-switch 里面的 skill 太多了，帮我合并一下。"},
                },
            ],
        )

        kb_record_codex_effectiveness.rebuild_logs(
            sessions_root=sessions_root,
            base_dir=kb_root,
            force=True,
        )

        row = read_jsonl(kb_record_codex_effectiveness.log_path(kb_root))[0]
        assert row["kb_expected"] is False
        assert row["effect_verdict"] == "no_kb_needed"


def test_generic_skill_maintenance_does_not_force_kb_expected() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        sessions_root = root / "sessions"
        kb_root = root / "repos"

        session = sessions_root / "2026" / "07" / "03" / "skill-maintenance.jsonl"
        write_jsonl(
            session,
            [
                {
                    "type": "session_meta",
                    "payload": {
                        "id": "skill-maintenance",
                        "timestamp": "2026-07-03T10:00:00+08:00",
                        "cwd": "/repo/demo",
                        "thread_source": "user",
                    },
                },
                {
                    "type": "event_msg",
                    "payload": {"type": "user_message", "message": "帮我整理 editor skill 和 diagram skill 的安装方式。"},
                },
            ],
        )

        kb_record_codex_effectiveness.rebuild_logs(
            sessions_root=sessions_root,
            base_dir=kb_root,
            force=True,
        )

        row = read_jsonl(kb_record_codex_effectiveness.log_path(kb_root))[0]
        assert row["generic_skill_install"] is True
        assert row["kb_expected"] is False
        assert row["effect_verdict"] == "no_kb_needed"


def test_storage_word_does_not_match_standalone_rag() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        sessions_root = root / "sessions"
        kb_root = root / "repos"

        session = sessions_root / "2026" / "07" / "03" / "storage.jsonl"
        write_jsonl(
            session,
            [
                {
                    "type": "session_meta",
                    "payload": {
                        "id": "storage-main",
                        "timestamp": "2026-07-03T10:00:00+08:00",
                        "cwd": "/repo/demo",
                        "thread_source": "user",
                    },
                },
                {
                    "type": "event_msg",
                    "payload": {"type": "user_message", "message": "检查 disable_response_storage 配置有没有问题。"},
                },
            ],
        )

        kb_record_codex_effectiveness.rebuild_logs(
            sessions_root=sessions_root,
            base_dir=kb_root,
            force=True,
        )

        row = read_jsonl(kb_record_codex_effectiveness.log_path(kb_root))[0]
        assert row["kb_expected"] is False
        assert row["effect_verdict"] == "no_kb_needed"


def test_reading_personal_kb_script_files_is_not_kb_usage() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        sessions_root = root / "sessions"
        kb_root = root / "repos"

        session = sessions_root / "2026" / "07" / "03" / "read-script.jsonl"
        write_jsonl(
            session,
            [
                {
                    "type": "session_meta",
                    "payload": {
                        "id": "read-script",
                        "timestamp": "2026-07-03T10:00:00+08:00",
                        "cwd": "/repo/demo",
                        "source": _subagent_source(parent_thread_id="parent-3", role="explorer"),
                        "thread_source": "subagent",
                    },
                },
                {
                    "type": "event_msg",
                    "payload": {
                        "type": "user_message",
                        "message": "只读检查 personal-kb skill 文件，不改文件，不要运行 personal-kb 脚本。",
                    },
                },
                _function_call(
                    "call-sed",
                    "sed -n '1,220p' /home/tester/project/skills/personal-kb/scripts/kb_rag_context.py",
                ),
                _function_output("call-sed", "Process exited with code 0\n"),
            ],
        )

        kb_record_codex_effectiveness.rebuild_logs(
            sessions_root=sessions_root,
            base_dir=kb_root,
            force=True,
        )

        row = read_jsonl(kb_record_codex_effectiveness.log_path(kb_root))[0]
        assert row["is_subagent"] is True
        assert row["forbid_kb"] is True
        assert row["kb_used"] is False
        assert row["subagent_kb_task"] is False
        assert row["effect_verdict"] == "subagent_no_kb_expected"


def test_log_scanner_mentions_of_kb_scripts_are_not_kb_usage() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        sessions_root = root / "sessions"
        kb_root = root / "repos"

        session = sessions_root / "2026" / "07" / "03" / "scanner.jsonl"
        write_jsonl(
            session,
            [
                {
                    "type": "session_meta",
                    "payload": {
                        "id": "scanner",
                        "timestamp": "2026-07-03T10:00:00+08:00",
                        "cwd": "/repo/demo",
                        "source": _subagent_source(parent_thread_id="parent-4", role="explorer"),
                        "thread_source": "subagent",
                    },
                },
                {
                    "type": "event_msg",
                    "payload": {
                        "type": "user_message",
                        "message": "只读分析 Codex 日志里的 personal-kb/RAG 调用，不要运行 personal-kb 脚本。",
                    },
                },
                _function_call(
                    "call-scan",
                    "python3 - <<'PY'\nscripts=['kb_rag_context.py','kb_search.py','kb_closeout.py']\nprint(scripts)\nPY",
                ),
                _function_output("call-scan", "Process exited with code 0\n['kb_rag_context.py']\n"),
            ],
        )

        kb_record_codex_effectiveness.rebuild_logs(
            sessions_root=sessions_root,
            base_dir=kb_root,
            force=True,
        )

        row = read_jsonl(kb_record_codex_effectiveness.log_path(kb_root))[0]
        assert row["kb_used"] is False
        assert row["effect_verdict"] == "subagent_no_kb_expected"


def test_direct_and_nested_kb_script_invocations_still_count() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        sessions_root = root / "sessions"
        kb_root = root / "repos"

        direct_session = sessions_root / "2026" / "07" / "03" / "direct.jsonl"
        write_jsonl(
            direct_session,
            [
                {
                    "type": "session_meta",
                    "payload": {
                        "id": "direct",
                        "timestamp": "2026-07-03T10:00:00+08:00",
                        "cwd": "/repo/demo",
                        "thread_source": "user",
                    },
                },
                {
                    "type": "event_msg",
                    "payload": {"type": "user_message", "message": "继续之前的问题排查"},
                },
                _function_call(
                    "call-rag",
                    "python3 /home/tester/.local/skills/personal-kb/scripts/kb_rag_context.py \"demo\" --limit 5",
                ),
                _function_output("call-rag", "Process exited with code 0\nKB_RAG_CONTEXT query=\"demo\" hits=1\n"),
            ],
        )

        nested_session = sessions_root / "2026" / "07" / "03" / "nested.jsonl"
        write_jsonl(
            nested_session,
            [
                {
                    "type": "session_meta",
                    "payload": {
                        "id": "nested",
                        "timestamp": "2026-07-03T10:01:00+08:00",
                        "cwd": "/repo/demo",
                        "thread_source": "user",
                    },
                },
                {
                    "type": "event_msg",
                    "payload": {"type": "user_message", "message": "继续之前的问题排查"},
                },
                _function_call(
                    "call-nested",
                    "python3 - <<'PY'\nimport subprocess\nsubprocess.run(['python3','/home/tester/.local/skills/personal-kb/scripts/kb_rag_context.py','demo'], check=True)\nPY",
                ),
                _function_output("call-nested", "Process exited with code 0\nKB_RAG_CONTEXT query=\"demo\" hits=1\n"),
            ],
        )

        kb_record_codex_effectiveness.rebuild_logs(
            sessions_root=sessions_root,
            base_dir=kb_root,
            force=True,
        )

        rows = read_jsonl(kb_record_codex_effectiveness.log_path(kb_root))
        by_id = {row["session_id"]: row for row in rows}
        assert by_id["direct"]["kb_used"] is True
        assert by_id["direct"]["kb_call_counts"]["kb_rag_context"] == 1
        assert by_id["nested"]["kb_used"] is True
        assert by_id["nested"]["kb_call_counts"]["kb_rag_context"] == 1


def test_custom_exec_calls_count_only_successful_kb_invocations() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        sessions_root = root / "sessions"
        kb_root = root / "repos"

        session = sessions_root / "2026" / "07" / "11" / "custom-exec.jsonl"
        write_jsonl(
            session,
            [
                {
                    "type": "session_meta",
                    "payload": {
                        "id": "custom-exec",
                        "timestamp": "2026-07-11T10:00:00+08:00",
                        "cwd": "/repo/demo",
                        "thread_source": "user",
                    },
                },
                {
                    "type": "event_msg",
                    "payload": {"type": "user_message", "message": "继续之前的 personal-kb 优化"},
                },
                _custom_exec_call(
                    "call-rag",
                    "python3 /home/tester/.local/skills/personal-kb/scripts/kb_rag_context.py \"demo\" --limit 5",
                ),
                _custom_exec_output(
                    "call-rag",
                    "Script completed\nWall time 0.1 seconds\nOutput:\nKB_RAG_CONTEXT query=\"demo\" hits=1\n",
                ),
                _custom_exec_call(
                    "call-read",
                    "sed -n '1,80p' /home/tester/.local/skills/personal-kb/scripts/kb_closeout.py",
                ),
                _custom_exec_output("call-read", "Script completed\nWall time 0.0 seconds\nOutput:\n"),
                _custom_exec_call(
                    "call-closeout-failed",
                    "python3 /home/tester/.local/skills/personal-kb/scripts/kb_closeout.py --query demo --hit-count 1 --rag-calls 1 --used entry-1",
                ),
                _custom_exec_output(
                    "call-closeout-failed",
                    "Script failed\nProcess exited with code 2\nOutput:\ninvalid arguments\n",
                ),
            ],
        )

        kb_record_codex_effectiveness.rebuild_logs(
            sessions_root=sessions_root,
            base_dir=kb_root,
            force=True,
        )

        row = read_jsonl(kb_record_codex_effectiveness.log_path(kb_root))[0]
        assert row["source_format_counts"] == {"custom_tool_call_exec": 3}
        assert row["parser_version"] == "codex-shell-v9"
        assert row["parser_coverage"] == 1.0
        assert row["unparsed_exec_count"] == 0
        assert row["detected_kb_call_count"] == 2
        assert row["failed_kb_call_count"] == 1
        assert row["execution_unknown_kb_call_count"] == 0
        assert row["kb_call_counts"] == {"kb_rag_context": 1}
        assert row["retrieval_used"] is True
        assert row["closeout_called"] is False

        summary = json.loads(kb_record_codex_effectiveness.summary_path(kb_root).read_text(encoding="utf-8"))
        assert summary["source_format_counts"] == {"custom_tool_call_exec": 3}
        assert summary["parser_coverage"] == 1.0
        assert summary["failed_kb_call_count"] == 1


def test_current_custom_exec_shell_command_fixture_is_counted() -> None:
    row = kb_record_codex_effectiveness._parse_session(CURRENT_SHELL_FIXTURE)

    assert row["source_format_counts"] == {"custom_tool_call_exec": 1}
    assert row["parser_version"] == "codex-shell-v9"
    assert row["parser_coverage"] == 1.0
    assert row["unparsed_exec_count"] == 0
    assert row["detected_kb_call_count"] == 1
    assert row["execution_unknown_kb_call_count"] == 0
    assert row["kb_call_counts"] == {"kb_rag_context": 1}
    assert row["rag_queries"] == ["current shell fixture"]
    assert row["retrieval_ids"] == ["fixture-current-shell-1"]


def test_main_subagent_only_forbid_does_not_mark_main_forbidden() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        sessions_root = root / "sessions"
        kb_root = root / "repos"

        session = sessions_root / "2026" / "07" / "03" / "main-subagent-forbid.jsonl"
        write_jsonl(
            session,
            [
                {
                    "type": "session_meta",
                    "payload": {
                        "id": "main-subagent-forbid",
                        "timestamp": "2026-07-03T10:00:00+08:00",
                        "cwd": "/repo/demo",
                        "thread_source": "user",
                    },
                },
                {
                    "type": "event_msg",
                    "payload": {
                        "type": "user_message",
                        "message": "主会话先调用 personal-kb，子 agent 不要运行 personal-kb 脚本。",
                    },
                },
                _function_call(
                    "call-rag",
                    "python3 /home/tester/.local/skills/personal-kb/scripts/kb_rag_context.py \"demo\" --limit 5",
                ),
                _function_output("call-rag", "Process exited with code 0\nKB_RAG_CONTEXT query=\"demo\" hits=0\n"),
            ],
        )

        kb_record_codex_effectiveness.rebuild_logs(
            sessions_root=sessions_root,
            base_dir=kb_root,
            force=True,
        )

        row = read_jsonl(kb_record_codex_effectiveness.log_path(kb_root))[0]
        assert row["forbid_kb_scope"] == "subagent_only"
        assert row["forbid_kb"] is False
        assert row["effect_verdict"] != "forbidden_but_used"


def test_parent_closeout_links_authorized_scout_retrieval_across_rollouts() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        sessions_root = root / "sessions"
        kb_root = root / "repos"
        parent_id = "parent-link-session"
        child_id = "scout-link-session"
        retrieval_id = "retrieval-link-1"

        parent = sessions_root / "2026" / "07" / "15" / "parent.jsonl"
        write_jsonl(
            parent,
            [
                {
                    "type": "session_meta",
                    "payload": {
                        "id": parent_id,
                        "timestamp": "2026-07-15T10:00:00+08:00",
                        "cwd": "/repo/demo",
                        "thread_source": "user",
                    },
                },
                {
                    "type": "event_msg",
                    "payload": {
                        "type": "user_message",
                        "message": "查以前多个项目的 Personal KB 路径映射。",
                    },
                },
                _function_call(
                    "parent-closeout",
                    "python3 kb_closeout.py --query 'old mapping' --rag-calls 1 "
                    "--hit-count 1 --allowed-hit-id entry-1 --used-locate entry-1 "
                    f"--linked-retrieval-id {retrieval_id} --stdout",
                ),
                _closeout_output(
                    "parent-closeout",
                    {
                        "event": "kb_closeout",
                        "status": "ok",
                        "linked_retrieval_ids": [retrieval_id],
                        "queries": ["old mapping"],
                        "rag_calls": 1,
                        "hit_count": 1,
                        "used_entry_ids": ["entry-1"],
                        "adopted_entry_ids": ["entry-1"],
                        "adoption_effects": {
                            "locate": ["entry-1"],
                            "decide": [],
                            "fix": [],
                            "write": [],
                        },
                    },
                ),
            ],
        )

        child = sessions_root / "2026" / "07" / "15" / "child.jsonl"
        write_jsonl(
            child,
            [
                {
                    "type": "session_meta",
                    "payload": {
                        "id": child_id,
                        "parent_thread_id": parent_id,
                        "timestamp": "2026-07-15T10:01:00+08:00",
                        "cwd": "/repo/demo",
                        "source": _subagent_source(parent_thread_id=parent_id, role="kb_scout"),
                        "thread_source": "subagent",
                    },
                },
                {
                    "type": "event_msg",
                    "payload": {
                        "type": "user_message",
                        "message": "Authorization: dedicated read-only Personal KB scout. 检索旧路径映射。",
                    },
                },
                _function_call("scout-rag", "python3 kb_rag_context.py 'old mapping'"),
                _function_output(
                    "scout-rag",
                    "Process exited with code 0\n"
                    f"KB_RAG_CONTEXT retrieval_id=\"{retrieval_id}\" query=\"old mapping\" hits=1\n",
                ),
            ],
        )

        result = kb_record_codex_effectiveness.rebuild_logs(
            sessions_root=sessions_root,
            base_dir=kb_root,
            force=True,
        )

        rows = {
            row["session_id"]: row
            for row in read_jsonl(kb_record_codex_effectiveness.log_path(kb_root))
        }
        parent_row = rows[parent_id]
        child_row = rows[child_id]
        assert child_row["parent_closeout_linked_retrieval_ids"] == [retrieval_id]
        assert child_row["delegated_closeout_linked"] is True
        assert child_row["effective_missing_closeout"] is False
        assert parent_row["delegated_retrieval_ids"] == [retrieval_id]
        assert parent_row["delegated_retrieval_call_count"] == 1
        assert parent_row["effective_retrieval_used"] is True
        assert parent_row["effect_verdict"] == "used_hit_adopted"
        assert result["summary"]["scout_retrieval_linked_to_parent_closeout_count"] == 1
        assert result["summary"]["orphan_scout_retrieval_id_count"] == 0


def test_unlinked_scout_retrieval_remains_orphan_without_query_guessing() -> None:
    rows = [
        {
            "session_id": "parent-1",
            "rollout_id": "parent-1",
            "is_subagent": False,
            "closeout_linked_retrieval_ids": [],
            "retrieval_events": [],
            "retrieval_used": False,
            "effect_verdict": "needed_but_not_used",
            "effect_score": -1,
        },
        {
            "session_id": "different-parent",
            "rollout_id": "different-parent",
            "is_subagent": False,
            "closeout_linked_retrieval_ids": ["orphan-id"],
            "retrieval_events": [],
            "retrieval_used": False,
            "effect_verdict": "no_kb_needed",
            "effect_score": 0,
        },
        {
            "session_id": "scout-1",
            "rollout_id": "scout-1",
            "is_subagent": True,
            "subagent_parent_thread_id": "parent-1",
            "subagent_kb_authorization": "authorized",
            "subagent_role": "kb_scout",
            "retrieval_events": [
                {
                    "retrieval_id": "orphan-id",
                    "query": "same query",
                    "hit_count": 1,
                }
            ],
            "retrieval_id_missing_count": 0,
            "unpaired_retrieval_call_count": 1,
        },
    ]

    stats = kb_record_codex_effectiveness._reconcile_parent_scout_links(rows)

    assert rows[2]["parent_closeout_linked_retrieval_ids"] == []
    assert rows[2]["orphan_scout_retrieval_ids"] == ["orphan-id"]
    assert rows[2]["effective_missing_closeout"] is True
    assert stats["orphan_scout_retrieval_id_count"] == 1


def test_duplicate_scout_retrieval_id_is_invalid_and_not_linked() -> None:
    rows = [
        {
            "session_id": "parent-1",
            "rollout_id": "parent-1",
            "is_subagent": False,
            "closeout_linked_retrieval_ids": ["duplicate-id"],
            "retrieval_events": [],
            "retrieval_used": False,
            "effect_verdict": "needed_but_not_used",
            "effect_score": -1,
        },
        *[
            {
                "session_id": f"scout-{index}",
                "rollout_id": f"scout-{index}",
                "is_subagent": True,
                "subagent_parent_thread_id": "parent-1",
                "subagent_kb_authorization": "authorized",
                "subagent_role": "kb_scout",
                "retrieval_events": [
                    {
                        "retrieval_id": "duplicate-id",
                        "query": "same query",
                        "hit_count": 1,
                    }
                ],
                "retrieval_id_missing_count": 0,
                "unpaired_retrieval_call_count": 1,
            }
            for index in (1, 2)
        ],
    ]

    stats = kb_record_codex_effectiveness._reconcile_parent_scout_links(rows)

    assert rows[0]["delegated_retrieval_call_count"] == 0
    assert rows[1]["invalid_scout_retrieval_ids"] == ["duplicate-id"]
    assert rows[2]["invalid_scout_retrieval_ids"] == ["duplicate-id"]
    assert stats["duplicate_retrieval_id_count"] == 1
    assert stats["invalid_scout_retrieval_id_count"] == 2


def test_rag_output_tokens_cannot_be_spoofed_by_query_text() -> None:
    output = (
        'KB_RAG_CONTEXT query="retrieval_id="fake" hits=999" '
        'retrieval_id="real-id" hits=0\n'
        '- note: retrieval_id="later-fake" hits=777\n'
        '- note: {"mode":"read_only_rag_context","retrieval_id":"json-fake",'
        '"hit_count":888,"items":[]}\n'
    )
    assert kb_record_codex_effectiveness._parse_retrieval_id_from_output(output) == "real-id"
    assert kb_record_codex_effectiveness._parse_hit_count_from_output(output) == 0

    invalid_json_id = json.dumps(
        {
            "mode": "read_only_rag_context",
            "retrieval_id": "invalid id with spaces",
            "hit_count": 1,
            "items": [{"entry_id": "entry-1"}],
        }
    )
    assert kb_record_codex_effectiveness._parse_retrieval_id_from_output(invalid_json_id) == ""


def test_same_rollout_pairing_prefers_exact_id_and_reserves_scout_links() -> None:
    retrieval = {
        "sequence": 0,
        "turn_id": "turn-1",
        "query": "local query",
        "retrieval_id": "local-id",
    }
    exact_closeout = {
        "sequence": 1,
        "turn_id": "turn-2",
        "queries": ["different query"],
        "linked_retrieval_ids": ["local-id"],
        "rag_calls": 1,
    }
    pairings, unmatched = kb_record_codex_effectiveness._match_retrieval_closeouts(
        [retrieval], [exact_closeout]
    )
    assert unmatched == []
    assert pairings[0]["match_kind"] == "retrieval_id"
    assert pairings[0]["retrieval_id"] == "local-id"

    scout_only_closeout = {
        "sequence": 1,
        "turn_id": "turn-1",
        "queries": [],
        "linked_retrieval_ids": ["scout-id"],
        "rag_calls": 1,
    }
    pairings, unmatched = kb_record_codex_effectiveness._match_retrieval_closeouts(
        [retrieval], [scout_only_closeout]
    )
    assert pairings == []
    assert unmatched == [retrieval]


def test_reconciliation_preserves_parent_errors_and_forbidden_scout_is_invalid() -> None:
    rows = [
        {
            "session_id": "parent-forbidden",
            "rollout_id": "parent-forbidden",
            "is_subagent": False,
            "closeout_linked_retrieval_ids": ["rid-forbidden-parent"],
            "retrieval_events": [],
            "retrieval_used": False,
            "forbid_kb": True,
            "forbidden_kb_used": True,
            "effect_verdict": "forbidden_but_used",
            "effect_score": -2,
            "unpaired_retrieval_call_count": 0,
        },
        {
            "session_id": "scout-forbidden-parent",
            "rollout_id": "scout-forbidden-parent",
            "is_subagent": True,
            "subagent_parent_thread_id": "parent-forbidden",
            "subagent_kb_authorization": "authorized",
            "subagent_role": "kb_scout",
            "retrieval_events": [
                {"retrieval_id": "rid-forbidden-parent", "query": "q", "hit_count": 1}
            ],
            "retrieval_id_missing_count": 0,
            "unpaired_retrieval_call_count": 1,
        },
        {
            "session_id": "parent-unpaired",
            "rollout_id": "parent-unpaired",
            "is_subagent": False,
            "closeout_linked_retrieval_ids": ["rid-unpaired-parent"],
            "retrieval_events": [],
            "retrieval_used": True,
            "missing_closeout": True,
            "effect_verdict": "used_without_closeout",
            "effect_score": 1,
            "unpaired_retrieval_call_count": 1,
        },
        {
            "session_id": "scout-unpaired-parent",
            "rollout_id": "scout-unpaired-parent",
            "is_subagent": True,
            "subagent_parent_thread_id": "parent-unpaired",
            "subagent_kb_authorization": "authorized",
            "subagent_role": "kb_scout",
            "retrieval_events": [
                {"retrieval_id": "rid-unpaired-parent", "query": "q", "hit_count": 1}
            ],
            "retrieval_id_missing_count": 0,
            "unpaired_retrieval_call_count": 1,
        },
        {
            "session_id": "parent-forbidden-scout",
            "rollout_id": "parent-forbidden-scout",
            "is_subagent": False,
            "closeout_linked_retrieval_ids": ["rid-forbidden-scout"],
            "retrieval_events": [],
            "retrieval_used": False,
            "effect_verdict": "needed_but_not_used",
            "effect_score": -1,
            "unpaired_retrieval_call_count": 0,
        },
        {
            "session_id": "forbidden-scout",
            "rollout_id": "forbidden-scout",
            "is_subagent": True,
            "subagent_parent_thread_id": "parent-forbidden-scout",
            "subagent_kb_authorization": "forbidden",
            "subagent_role": "kb_scout",
            "forbid_kb": True,
            "retrieval_events": [
                {"retrieval_id": "rid-forbidden-scout", "query": "q", "hit_count": 1}
            ],
            "retrieval_id_missing_count": 0,
            "unpaired_retrieval_call_count": 1,
        },
    ]

    stats = kb_record_codex_effectiveness._reconcile_parent_scout_links(rows)

    assert rows[0]["effect_verdict"] == "forbidden_but_used"
    assert rows[2]["effect_verdict"] == "used_without_closeout"
    assert rows[2]["missing_closeout"] is True
    assert rows[5]["parent_closeout_linked_retrieval_ids"] == []
    assert rows[5]["invalid_scout_retrieval_ids"] == ["rid-forbidden-scout"]
    assert stats["invalid_scout_retrieval_id_count"] == 1


def test_reconciliation_reports_dangling_links_and_effective_unpaired_totals() -> None:
    rows = [
        {
            "session_id": "parent-1",
            "rollout_id": "parent-1",
            "is_subagent": False,
            "closeout_linked_retrieval_ids": ["linked-id", "missing-id"],
            "retrieval_events": [],
            "retrieval_used": False,
            "effect_verdict": "needed_but_not_used",
            "effect_score": -1,
            "unpaired_retrieval_call_count": 0,
        },
        {
            "session_id": "scout-1",
            "rollout_id": "scout-1",
            "is_subagent": True,
            "subagent_parent_thread_id": "parent-1",
            "subagent_kb_authorization": "authorized",
            "subagent_role": "kb_scout",
            "retrieval_events": [
                {"retrieval_id": "linked-id", "query": "q", "hit_count": 1}
            ],
            "retrieval_id_missing_count": 0,
            "unpaired_retrieval_call_count": 1,
            "effect_verdict": "subagent_kb_used",
        },
    ]

    stats = kb_record_codex_effectiveness._reconcile_parent_scout_links(rows)
    summary = kb_record_codex_effectiveness._summary_from_rows(rows)

    assert stats["dangling_parent_linked_retrieval_id_count"] == 1
    assert stats["dangling_parent_linked_retrieval_ids_sample"] == ["missing-id"]
    assert rows[1]["effective_unpaired_retrieval_call_count"] == 0
    assert summary["unpaired_retrieval_call_count"] == 0
    assert summary["raw_unpaired_retrieval_call_count"] == 1


def main() -> int:
    tests = [
        test_record_codex_effectiveness_writes_searchable_log_and_summary,
        test_subagent_history_task_does_not_count_as_main_missed_kb,
        test_subagent_forbidden_kb_usage_is_reported_separately,
        test_subagent_unexpected_kb_usage_is_reported_separately,
        test_quality_gate_fails_on_subagent_unexpected_kb_usage,
        test_quality_gate_passes_when_subagent_has_no_kb_usage,
        test_quality_gate_fails_on_missing_subagent_kb_guard,
        test_legacy_rows_are_archived_and_excluded_from_current_summary,
        test_duplicate_session_ids_are_collapsed_to_strongest_signal,
        test_rollout_uuid_selects_own_meta_from_inherited_multi_meta_session,
        test_dedupe_keeps_main_and_subagent_with_same_session_id_separate,
        test_subagent_project_message_guard_is_observed_without_polluting_user_excerpt,
        test_four_adoption_effect_flags_and_legacy_used_are_aggregated,
        test_actual_closeout_output_excludes_failed_and_session_brief_from_long_term_adoption,
        test_silent_closeout_keeps_requested_adoption_unconfirmed,
        test_later_unclosed_retrieval_is_not_hidden_by_earlier_closeout,
        test_subagent_kb_usage_with_unavailable_child_task_is_authorization_unknown,
        test_runtime_audit_and_current_incident_do_not_require_long_term_rag,
        test_help_only_kb_script_invocation_is_not_counted_as_kb_usage,
        test_generic_skill_word_no_longer_forces_kb_expected,
        test_generic_skill_maintenance_does_not_force_kb_expected,
        test_storage_word_does_not_match_standalone_rag,
        test_reading_personal_kb_script_files_is_not_kb_usage,
        test_log_scanner_mentions_of_kb_scripts_are_not_kb_usage,
        test_direct_and_nested_kb_script_invocations_still_count,
        test_custom_exec_calls_count_only_successful_kb_invocations,
        test_current_custom_exec_shell_command_fixture_is_counted,
        test_main_subagent_only_forbid_does_not_mark_main_forbidden,
        test_parent_closeout_links_authorized_scout_retrieval_across_rollouts,
        test_unlinked_scout_retrieval_remains_orphan_without_query_guessing,
        test_duplicate_scout_retrieval_id_is_invalid_and_not_linked,
        test_rag_output_tokens_cannot_be_spoofed_by_query_text,
        test_same_rollout_pairing_prefers_exact_id_and_reserves_scout_links,
        test_reconciliation_preserves_parent_errors_and_forbidden_scout_is_invalid,
        test_reconciliation_reports_dangling_links_and_effective_unpaired_totals,
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

    print("kb_record_codex_effectiveness tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
