#!/usr/bin/env python3
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from kb_codex_audit import audit_root


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def main() -> int:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        write_jsonl(
            root / "main.jsonl",
            [
                {"text": "plain text mentioning kb_search.py should not count"},
                {
                    "response_item": {
                        "payload": {
                            "type": "function_call",
                            "call_id": "call-search",
                            "name": "shell_command",
                            "arguments": json.dumps({"command": "python kb_search.py query"}),
                        }
                    }
                },
                {
                    "response_item": {
                        "payload": {
                            "type": "function_call_output",
                            "call_id": "call-search",
                            "output": "Exit code: 0\nok",
                        }
                    }
                },
                {
                    "response_item": {
                        "payload": {
                            "type": "function_call",
                            "call_id": "call-add",
                            "name": "shell_command",
                            "arguments": json.dumps({"command": "python kb_add.py --json '{bad}'"}),
                        }
                    }
                },
                {
                    "response_item": {
                        "payload": {
                            "type": "function_call_output",
                            "call_id": "call-add",
                            "output": "Exit code: 2\nInvalid JSON",
                        }
                    }
                },
                {
                    "response_item": {
                        "payload": {
                            "type": "function_call",
                            "call_id": "call-agent",
                            "name": "spawn_agent",
                            "arguments": json.dumps(
                                {
                                    "message": "背景知识（来自主会话 KB 历史，禁止你自行执行 KB 脚本）：不得执行 kb_search.py、kb_add.py"
                                }
                            ),
                        }
                    }
                },
            ],
        )

        result = audit_root(root)

    assert result["files"] == 1
    assert result["kb_search_calls"] == 1
    assert result["kb_search_success"] == 1
    assert result["kb_add_calls"] == 1
    assert result["kb_add_failures"] == 1
    assert result["inline_json_calls"] == 1
    assert result["spawn_agent_calls"] == 1
    assert result["spawn_agent_with_kb_background"] == 1
    assert result["spawn_agent_forbid_kb_search"] == 1
    assert result["spawn_agent_forbid_kb_add"] == 1
    print("kb_codex_audit tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
