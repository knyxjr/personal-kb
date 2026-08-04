#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import tempfile
from datetime import date, timedelta
from pathlib import Path

import kb_audit_codex_sessions as audit
from kb_audit_codex_sessions import build_report


CURRENT_SHELL_FIXTURE = (
    Path(__file__).with_name("fixtures") / "codex_custom_exec_shell_command.jsonl"
)


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def _tool_call(call_id: str, command: str, turn_id: str) -> dict:
    return {
        "type": "response_item",
        "payload": {
            "type": "function_call",
            "name": "exec",
            "call_id": call_id,
            "arguments": json.dumps({"cmd": command}),
            "internal_chat_message_metadata_passthrough": {"turn_id": turn_id},
        },
    }


def _tool_output(call_id: str) -> dict:
    return {
        "type": "response_item",
        "payload": {
            "type": "function_call_output",
            "call_id": call_id,
            "output": "Script completed\n",
        },
    }


def _tool_output_text(call_id: str, output: str) -> dict:
    row = _tool_output(call_id)
    row["payload"]["output"] = output
    return row


def test_parent_scout_retrieval_link_avoids_false_missing_reports() -> None:
    audit_day = date.today()
    parent_id = "33333333-3333-7333-8333-333333333333"
    child_id = "44444444-4444-7444-8444-444444444444"
    retrieval_id = "audit-link-1"
    with tempfile.TemporaryDirectory() as temp_dir:
        temp = Path(temp_dir)
        day_dir = temp / "sessions" / f"{audit_day:%Y}" / f"{audit_day:%m}" / f"{audit_day:%d}"
        _write_jsonl(
            day_dir / f"rollout-{audit_day.isoformat()}T10-00-00-{parent_id}.jsonl",
            [
                {
                    "type": "session_meta",
                    "payload": {"id": parent_id, "thread_source": "user", "source": "cli"},
                },
                {
                    "type": "event_msg",
                    "payload": {"type": "user_message", "message": "查以前多个项目的 Personal KB 记录。"},
                },
                _tool_call(
                    "closeout",
                    "python3 /tmp/kb_closeout.py --query 'old mapping' --rag-calls 1 "
                    f"--hit-count 1 --reason recorded --linked-retrieval-id {retrieval_id}",
                    "turn-parent",
                ),
                _tool_output("closeout"),
            ],
        )
        _write_jsonl(
            day_dir / f"rollout-{audit_day.isoformat()}T10-01-00-{child_id}.jsonl",
            [
                {
                    "type": "session_meta",
                    "payload": {
                        "id": child_id,
                        "thread_source": "subagent",
                        "parent_thread_id": parent_id,
                        "source": {
                            "subagent": {
                                "thread_spawn": {
                                    "parent_thread_id": parent_id,
                                    "agent_role": "kb_scout",
                                }
                            }
                        },
                    },
                },
                {
                    "type": "event_msg",
                    "payload": {"type": "user_message", "message": "KB scout 检索旧路径映射。"},
                },
                _tool_call("rag", "python3 /tmp/kb_rag_context.py 'old mapping'", "turn-child"),
                _tool_output_text(
                    "rag",
                    "Script completed\n"
                    f"KB_RAG_CONTEXT retrieval_id=\"{retrieval_id}\" query=\"old mapping\" hits=1\n",
                ),
            ],
        )
        closeout = temp / "closeout.jsonl"
        closeout.write_text("", encoding="utf-8")
        report = build_report(
            argparse.Namespace(
                sessions_root=str(temp / "sessions"),
                date=[audit_day.isoformat()],
                last_days=0,
                closeout=str(closeout),
                examples=10,
            )
        )

    assert report["main_missed_rag_sessions"] == 0
    assert report["rag_without_closeout_sessions"] == 0
    assert report["parent_sessions_with_delegated_retrieval"] == 1
    assert report["scout_retrieval_linked_to_parent_closeout_count"] == 1
    assert report["orphan_scout_retrieval_id_count"] == 0


def test_audit_parser_uses_canonical_header_id() -> None:
    output = (
        'KB_RAG_CONTEXT query="retrieval_id="fake"" retrieval_id="real-id" hits=0\n'
        '- note: retrieval_id="later-fake"\n'
        '- note: {"mode":"read_only_rag_context","retrieval_id":"json-fake"}\n'
    )
    assert audit._retrieval_id_from_output(output) == "real-id"
    invalid_json = json.dumps(
        {"mode": "read_only_rag_context", "retrieval_id": "invalid id"}
    )
    assert audit._retrieval_id_from_output(invalid_json) == ""


def test_current_custom_exec_shell_command_fixture_and_legacy_compatibility() -> None:
    session = audit._parse_session(CURRENT_SHELL_FIXTURE)

    assert session["source_format_counts"] == {"custom_tool_call_exec": 1}
    assert session["parser_version"] == "codex-shell-v9"
    assert session["parser_coverage"] == 1.0
    assert session["unparsed_exec_count"] == 0
    assert session["detected_kb_call_count"] == 1
    assert session["execution_unknown_kb_call_count"] == 0
    assert session["counters"] == {"kb_rag_context": 1}
    assert session["rag_queries_without_closeout"] == ["current shell fixture"]
    assert session["retrieval_events"][0]["retrieval_id"] == "fixture-current-shell-1"

    legacy_command = "python3 /tmp/kb_search.py legacy-query"
    legacy_source = (
        "const r = await tools.exec_command("
        + json.dumps({"cmd": legacy_command})
        + "); text(r.output);"
    )
    assert audit._custom_exec_commands(legacy_source) == [legacy_command]


def test_audit_same_rollout_pairing_prefers_id_and_reserves_scout_capacity() -> None:
    rag = {
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
        "capacity": 1,
    }
    matched, unmatched = audit._match_rag_closeouts([rag], [exact_closeout])
    assert matched == 1
    assert unmatched == []

    scout_only_closeout = {
        "sequence": 1,
        "turn_id": "turn-1",
        "queries": [],
        "linked_retrieval_ids": ["scout-id"],
        "capacity": 1,
    }
    matched, unmatched = audit._match_rag_closeouts([rag], [scout_only_closeout])
    assert matched == 0
    assert unmatched == [rag]


def test_audit_reconciliation_rejects_forbidden_scout_and_reports_dangling_link() -> None:
    sessions = [
        {
            "session_id": "parent-1",
            "is_main": True,
            "is_subagent": False,
            "closeout_linked_retrieval_ids": ["forbidden-id", "missing-id"],
            "retrieval_events": [],
            "rag_or_search": 0,
        },
        {
            "session_id": "scout-1",
            "is_main": False,
            "is_subagent": True,
            "subagent_parent_thread_id": "parent-1",
            "forbid_kb": True,
            "retrieval_events": [
                {"retrieval_id": "forbidden-id", "query": "q"}
            ],
            "rag_calls_without_closeout": 1,
            "retrieval_id_missing_count": 0,
        },
    ]
    stats = audit._reconcile_parent_scout_sessions(sessions)
    assert sessions[1]["parent_closeout_linked_retrieval_ids"] == []
    assert sessions[1]["invalid_scout_retrieval_ids"] == ["forbidden-id"]
    assert stats["invalid_scout_retrieval_id_count"] == 1
    assert stats["dangling_parent_linked_retrieval_ids_sample"] == ["missing-id"]


def _session_rows(
    session_id: str,
    *,
    source: str,
    rag_query: str,
    closeout_query: str,
    parent_meta: dict | None = None,
) -> list[dict]:
    turn_id = f"turn-{session_id[:8]}"
    rows = [
        {
            "type": "session_meta",
            "payload": {
                "id": session_id,
                "thread_source": source,
                "source": (
                    {"subagent": {"thread_spawn": {"parent_thread_id": "parent-id"}}}
                    if source == "subagent"
                    else "cli"
                ),
                "parent_thread_id": "parent-id" if source == "subagent" else "",
            },
        },
    ]
    if parent_meta:
        rows.append({"type": "session_meta", "payload": parent_meta})
    rows.extend(
        [
            {"type": "turn_context", "payload": {"turn_id": turn_id}},
            {
                "type": "event_msg",
                "payload": {"type": "user_message", "message": "检查历史简历资料"},
            },
            _tool_call(
                "rag",
                f"python3 /tmp/kb_rag_context.py {json.dumps(rag_query, ensure_ascii=False)}",
                turn_id,
            ),
            _tool_output("rag"),
            _tool_call(
                "closeout",
                "python3 /tmp/kb_closeout.py "
                f"--query {json.dumps(closeout_query, ensure_ascii=False)} "
                "--hit-count 1 --rag-calls 1 --reason recorded",
                turn_id,
            ),
            _tool_output("closeout"),
        ]
    )
    return rows


def main() -> int:
    audit_day = date.today()
    old_day = audit_day - timedelta(days=5)
    root_id = "11111111-1111-7111-8111-111111111111"
    subagent_id = "22222222-2222-7222-8222-222222222222"

    with tempfile.TemporaryDirectory() as temp_dir:
        temp = Path(temp_dir)
        sessions_root = temp / "sessions"
        day_dir = sessions_root / f"{audit_day:%Y}" / f"{audit_day:%m}" / f"{audit_day:%d}"
        _write_jsonl(
            day_dir / f"rollout-{audit_day.isoformat()}T10-00-00-{root_id}.jsonl",
            _session_rows(root_id, source="user", rag_query="same query", closeout_query="same query"),
        )
        _write_jsonl(
            day_dir / f"rollout-{audit_day.isoformat()}T10-01-00-{subagent_id}.jsonl",
            _session_rows(
                subagent_id,
                source="subagent",
                rag_query="unclosed query",
                closeout_query="different query",
                parent_meta={"id": root_id, "thread_source": "user", "source": "cli"},
            ),
        )
        current_only_id = "33333333-3333-7333-8333-333333333333"
        _write_jsonl(
            day_dir / f"rollout-{audit_day.isoformat()}T10-02-00-{current_only_id}.jsonl",
            [
                {
                    "type": "session_meta",
                    "payload": {
                        "id": current_only_id,
                        "thread_source": "user",
                        "source": "cli",
                    },
                },
                {
                    "type": "event_msg",
                    "payload": {
                        "type": "user_message",
                        "message": "重构当前前端，可能需要 skill，直接以当前代码和浏览器为准",
                    },
                },
            ],
        )
        runtime_audit_id = "44444444-4444-7444-8444-444444444444"
        _write_jsonl(
            day_dir / f"rollout-{audit_day.isoformat()}T10-03-00-{runtime_audit_id}.jsonl",
            [
                {
                    "type": "session_meta",
                    "payload": {
                        "id": runtime_audit_id,
                        "thread_source": "user",
                        "source": "cli",
                    },
                },
                {
                    "type": "event_msg",
                    "payload": {
                        "type": "user_message",
                        "message": "审计 Personal KB 这两天的运行效果，直接读取 session/runtime 日志",
                    },
                },
            ],
        )
        current_incident_id = "55555555-5555-7555-8555-555555555555"
        _write_jsonl(
            day_dir / f"rollout-{audit_day.isoformat()}T10-04-00-{current_incident_id}.jsonl",
            [
                {
                    "type": "session_meta",
                    "payload": {
                        "id": current_incident_id,
                        "thread_source": "user",
                        "source": "cli",
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
        help_only_id = "66666666-6666-7666-8666-666666666666"
        _write_jsonl(
            day_dir / f"rollout-{audit_day.isoformat()}T10-05-00-{help_only_id}.jsonl",
            [
                {
                    "type": "session_meta",
                    "payload": {
                        "id": help_only_id,
                        "thread_source": "user",
                        "source": "cli",
                    },
                },
                {
                    "type": "event_msg",
                    "payload": {"type": "user_message", "message": "查看当前脚本帮助"},
                },
                _tool_call("help", "python3 /tmp/kb_search.py --help", "turn-help"),
                _tool_output("help"),
            ],
        )

        closeout_path = temp / "closeout.jsonl"
        current_ts = f"{audit_day.isoformat()}T12:00:00+08:00"
        _write_jsonl(
            closeout_path,
            [
                {
                    "ts": f"{old_day.isoformat()}T12:00:00+08:00",
                    "hit_count": 1,
                    "rag_calls": 1,
                    "skipped_reason": "",
                    "adoption_effects": {"decide": ["old"]},
                    "heated_entry_ids": [],
                },
                {
                    "ts": current_ts,
                    "hit_count": 1,
                    "rag_calls": 1,
                    "skipped_reason": "hits were irrelevant",
                },
                {
                    "ts": current_ts,
                    "hit_count": 1,
                    "rag_calls": 1,
                    "skipped_reason": "",
                },
                {
                    "ts": current_ts,
                    "hit_count": 1,
                    "rag_calls": 1,
                    "used_entry_ids": ["locate-id"],
                    "adoption_effects": {"locate": ["locate-id"]},
                    "heated_entry_ids": [],
                    "skipped_reason": "",
                },
                {
                    "ts": current_ts,
                    "hit_count": 1,
                    "rag_calls": 1,
                    "used_entry_ids": ["decide-id"],
                    "adoption_effects": {"decide": ["decide-id"]},
                    "heated_entry_ids": [],
                    "skipped_reason": "",
                },
                {
                    "ts": current_ts,
                    "hit_count": 1,
                    "rag_calls": 1,
                    "used_entry_ids": ["fix-id"],
                    "adoption_effects": {"fix": ["fix-id"]},
                    "heated_entry_ids": ["fix-id"],
                    "skipped_reason": "",
                },
            ],
        )

        report = build_report(
            argparse.Namespace(
                sessions_root=str(sessions_root),
                date=[],
                last_days=2,
                closeout=str(closeout_path),
                examples=10,
            )
        )

    assert report["session_files"] == 6
    assert report["sessions_by_source"] == {"user": 5, "subagent": 1}
    assert report["main_sessions"] == 5
    assert report["subagent_sessions"] == 1
    assert report["main_missed_rag_sessions"] == 0
    assert report["rag_without_closeout_sessions"] == 1
    assert report["call_totals"].get("kb_search", 0) == 0
    unmatched = report["examples"]["rag_without_closeout"]
    assert unmatched[0]["session_id"] == subagent_id
    assert unmatched[0]["rag_queries_without_closeout"] == ["unclosed query"]

    issues = report["closeout_issues"]
    assert issues["rows"] == 5
    assert issues["hit_with_no_action"] == 1
    assert issues["used_without_heat"] == 1
    test_parent_scout_retrieval_link_avoids_false_missing_reports()
    test_audit_parser_uses_canonical_header_id()
    test_current_custom_exec_shell_command_fixture_and_legacy_compatibility()
    test_audit_same_rollout_pairing_prefers_id_and_reserves_scout_capacity()
    test_audit_reconciliation_rejects_forbidden_scout_and_reports_dangling_link()
    print("kb_audit_codex_sessions tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
