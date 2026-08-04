from __future__ import annotations

import ast
import json
import re
from dataclasses import dataclass


_CUSTOM_TOOL_CALL_RE = re.compile(
    r"(?<![A-Za-z0-9_$])tools\s*\.\s*(exec_command|shell_command|apply_patch)\s*\("
)


@dataclass(frozen=True)
class CustomExecInvocation:
    tool_name: str
    command: str = ""


def _string_end(source: str, start: int) -> int:
    quote = source[start]
    index = start + 1
    while index < len(source):
        if source[index] == "\\":
            index += 2
            continue
        if source[index] == quote:
            return index + 1
        index += 1
    return len(source)


def _skip_trivia(source: str, start: int) -> int:
    index = start
    while index < len(source):
        if source[index].isspace():
            index += 1
            continue
        if source.startswith("//", index):
            newline = source.find("\n", index + 2)
            index = len(source) if newline < 0 else newline + 1
            continue
        if source.startswith("/*", index):
            end = source.find("*/", index + 2)
            index = len(source) if end < 0 else end + 2
            continue
        break
    return index


def _code_mask(source: str) -> str:
    masked = list(source)
    index = 0
    while index < len(source):
        if source[index] in {"'", '"', "`"}:
            end = _string_end(source, index)
            masked[index:end] = " " * (end - index)
            index = end
            continue
        if source.startswith("//", index):
            end = source.find("\n", index + 2)
            end = len(source) if end < 0 else end
            masked[index:end] = " " * (end - index)
            index = end
            continue
        if source.startswith("/*", index):
            end = source.find("*/", index + 2)
            end = len(source) if end < 0 else end + 2
            masked[index:end] = " " * (end - index)
            index = end
            continue
        index += 1
    return "".join(masked)


def _matching_paren(masked: str, opening: int) -> int:
    depth = 0
    for index in range(opening, len(masked)):
        if masked[index] == "(":
            depth += 1
        elif masked[index] == ")":
            depth -= 1
            if depth == 0:
                return index
    return len(masked)


def _decode_string(literal: str) -> str:
    if literal.startswith('"'):
        try:
            value = json.loads(literal)
        except json.JSONDecodeError:
            return ""
        return str(value) if isinstance(value, str) else ""
    if literal.startswith("'"):
        try:
            value = ast.literal_eval(literal)
        except (SyntaxError, ValueError):
            return ""
        return str(value) if isinstance(value, str) else ""
    if literal.startswith("`") and literal.endswith("`"):
        value = literal[1:-1]
        if "${" in value:
            return ""
        return (
            value.replace(r"\`", "`")
            .replace(r"\n", "\n")
            .replace(r"\r", "\r")
            .replace(r"\t", "\t")
        )
    return ""


def _skip_value(source: str, start: int) -> int:
    closing: list[str] = []
    index = start
    pairs = {"(": ")", "[": "]", "{": "}"}
    while index < len(source):
        index = _skip_trivia(source, index)
        if index >= len(source):
            return index
        char = source[index]
        if char in {"'", '"', "`"}:
            index = _string_end(source, index)
            continue
        if char in pairs:
            closing.append(pairs[char])
            index += 1
            continue
        if closing and char == closing[-1]:
            closing.pop()
            index += 1
            continue
        if not closing and char in {",", "}"}:
            return index + 1 if char == "," else index
        index += 1
    return index


def _object_string_property(argument: str, names: set[str]) -> str:
    index = _skip_trivia(argument, 0)
    if index >= len(argument) or argument[index] != "{":
        return ""
    index += 1
    while index < len(argument):
        index = _skip_trivia(argument, index)
        if index >= len(argument) or argument[index] == "}":
            return ""

        key = ""
        if argument[index] in {"'", '"'}:
            end = _string_end(argument, index)
            key = _decode_string(argument[index:end])
            index = end
        elif argument[index].isalpha() or argument[index] in {"_", "$"}:
            end = index + 1
            while end < len(argument) and (
                argument[end].isalnum() or argument[end] in {"_", "$"}
            ):
                end += 1
            key = argument[index:end]
            index = end
        else:
            index = _skip_value(argument, index)
            continue

        index = _skip_trivia(argument, index)
        if index >= len(argument) or argument[index] != ":":
            index = _skip_value(argument, index)
            continue
        index = _skip_trivia(argument, index + 1)
        if key in names and index < len(argument) and argument[index] in {"'", '"', "`"}:
            end = _string_end(argument, index)
            return _decode_string(argument[index:end])
        index = _skip_value(argument, index)
    return ""


def custom_exec_invocations(source: str) -> list[CustomExecInvocation]:
    if not source:
        return []
    masked = _code_mask(source)
    invocations: list[CustomExecInvocation] = []
    for match in _CUSTOM_TOOL_CALL_RE.finditer(masked):
        tool_name = match.group(1)
        opening = match.end() - 1
        closing = _matching_paren(masked, opening)
        argument = source[opening + 1 : closing]
        command = ""
        if tool_name == "shell_command":
            command = _object_string_property(argument, {"command"})
        elif tool_name == "exec_command":
            command = _object_string_property(argument, {"cmd"})
        invocations.append(CustomExecInvocation(tool_name=tool_name, command=command))
    return invocations


def custom_exec_commands(source: str) -> list[str]:
    return [item.command for item in custom_exec_invocations(source) if item.command]


def custom_exec_is_parseable(source: str) -> bool:
    invocations = custom_exec_invocations(source)
    if not invocations:
        return False
    return all(item.tool_name == "apply_patch" or bool(item.command) for item in invocations)
