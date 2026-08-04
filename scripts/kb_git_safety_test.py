#!/usr/bin/env python3
from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable, Iterator
from unittest.mock import patch


_ORIGINAL_PLATFORM = sys.platform
sys.platform = "test"
try:
    import kb_closeout
    import kb_evidence
    import kb_lib
    import kb_rag_context
    import kb_search
    import kb_update
finally:
    sys.platform = _ORIGINAL_PLATFORM


REPO_NAME = "demo"
BRANCH = "main"
ENTRY_ID = "git-safety-entry"
REVISION_EXCLUDED_FIELDS = frozenset({"record_rev", "used_count", "last_used_ts"})


def _canonical_entry_revision(entry: dict[str, Any]) -> str:
    durable = {
        key: value
        for key, value in entry.items()
        if key not in REVISION_EXCLUDED_FIELDS
    }
    payload = json.dumps(
        durable,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def _read_raw_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _kb_path(root: Path) -> Path:
    return root / "repos" / REPO_NAME / BRANCH / "kb.jsonl"


def _adoption_path(root: Path) -> Path:
    return root / "repos" / "_meta" / "adoption_events.jsonl"


@contextlib.contextmanager
def _isolated_kb(root: Path, *, cwd: Path | None = None) -> Iterator[None]:
    previous_cwd = Path.cwd()
    target_cwd = cwd or root
    target_cwd.mkdir(parents=True, exist_ok=True)
    with patch.dict(os.environ, {"PERSONAL_KB_ROOT": str(root)}, clear=False):
        os.chdir(target_cwd)
        try:
            yield
        finally:
            os.chdir(previous_cwd)


def _call_main(func: Callable[[list[str]], int], argv: list[str]) -> tuple[int, str, str]:
    stdout = io.StringIO()
    stderr = io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        try:
            rc = func(argv)
        except SystemExit as exc:
            rc = exc.code if isinstance(exc.code, int) else 1
    return rc, stdout.getvalue(), stderr.getvalue()


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return result.stdout.strip()


def _make_git_repo(root: Path) -> tuple[Path, dict[str, Any]]:
    repo = root / "workspace"
    repo.mkdir(parents=True)
    _git(repo, "init", "-b", BRANCH)
    _git(repo, "config", "user.name", "Personal KB Test")
    _git(repo, "config", "user.email", "personal-kb-test@example.invalid")
    _git(repo, "remote", "add", "origin", "https://example.invalid/acme/demo.git")

    source = repo / "src" / "config.txt"
    source.parent.mkdir(parents=True)
    source.write_text("version=1\n", encoding="utf-8")
    _git(repo, "add", "src/config.txt")
    _git(repo, "commit", "-m", "initial evidence")

    snapshot = kb_evidence.capture_evidence_snapshots(
        {"source_paths": ["src/config.txt"]},
        repo,
    )[0]
    assert snapshot["type"] == "git_file"
    assert snapshot["worktree_state"] == "clean"
    return repo, snapshot


def _entry(*, repo: Path | None = None, snapshot: dict[str, Any] | None = None) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "id": ENTRY_ID,
        "schema_version": 2,
        "ts": "2026-07-01T00:00:00+00:00",
        "updated_ts": "2026-07-01T00:00:00+00:00",
        "kind": "implementation",
        "repo": REPO_NAME,
        "branch": BRANCH,
        "title": "Git evidence snapshot freshness sentinel",
        "story": "A durable conclusion backed by a Git file snapshot.",
        "tags": ["git-safety", "freshness"],
        "aliases": ["evidence snapshot", "freshness sentinel"],
        "trigger_terms": ["git evidence snapshot", "snapshot freshness sentinel"],
        "source_paths": ["src/config.txt"],
        "status": "current",
        "used_count": 7,
        "last_used_ts": "2026-07-01T01:00:00+00:00",
    }
    if repo is not None:
        entry["workspace_dir"] = str(repo)
    if snapshot is not None:
        entry["evidence_snapshots"] = [snapshot]
    entry["record_rev"] = _canonical_entry_revision(entry)
    return entry


def test_read_jsonl_rejects_git_conflict_markers() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        path = _kb_path(root)
        path.parent.mkdir(parents=True)
        path.write_text(
            "<<<<<<< HEAD\n"
            '{"id":"ours","kind":"experience","title":"ours"}\n'
            "=======\n"
            '{"id":"theirs","kind":"experience","title":"theirs"}\n'
            ">>>>>>> feature/team-update\n",
            encoding="utf-8",
        )

        with _isolated_kb(root):
            try:
                kb_lib.read_jsonl(path)
            except (RuntimeError, ValueError) as exc:
                message = str(exc).lower()
                assert "conflict" in message or "<<<<<<<" in message
            else:
                raise AssertionError("read_jsonl must fail closed instead of returning partial rows")


def test_update_requires_matching_expected_rev_and_rotates_revision() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        path = _kb_path(root)
        row = _entry()
        original_rev = str(row["record_rev"])
        _write_jsonl(path, [row])
        original_bytes = path.read_bytes()

        with _isolated_kb(root):
            bad_rc, _bad_out, bad_err = _call_main(
                kb_update.main,
                [
                    "update",
                    ENTRY_ID,
                    "--repo",
                    REPO_NAME,
                    "--branch",
                    BRANCH,
                    "--expected-rev",
                    "0" * 64,
                    "--json",
                    json.dumps({"story": "must not be persisted"}),
                ],
            )
            assert bad_rc != 0
            assert "rev" in bad_err.lower()
            assert path.read_bytes() == original_bytes

            good_rc, _good_out, good_err = _call_main(
                kb_update.main,
                [
                    "update",
                    ENTRY_ID,
                    "--repo",
                    REPO_NAME,
                    "--branch",
                    BRANCH,
                    "--expected-rev",
                    original_rev,
                    "--json",
                    json.dumps({"story": "verified current implementation"}),
                ],
            )

        assert good_rc == 0, good_err
        updated = _read_raw_jsonl(path)[0]
        assert updated["story"] == "verified current implementation"
        assert updated["ts"] == row["ts"]
        assert isinstance(updated.get("record_rev"), str)
        assert updated["record_rev"]
        assert updated["record_rev"] != original_rev


def test_update_use_appends_runtime_event_without_rewriting_durable_record() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        path = _kb_path(root)
        _write_jsonl(path, [_entry()])
        original_bytes = path.read_bytes()

        with _isolated_kb(root):
            rc, stdout, stderr = _call_main(
                kb_update.main,
                [
                    "use",
                    ENTRY_ID,
                    "--repo",
                    REPO_NAME,
                    "--branch",
                    BRANCH,
                ],
            )

        assert rc == 0, stderr or stdout
        assert path.read_bytes() == original_bytes
        events = _read_raw_jsonl(_adoption_path(root))
        assert len(events) == 1
        event = events[0]
        assert event["event"] == "kb_adoption"
        assert event["entry_id"] == ENTRY_ID
        assert event["effect"] == "legacy"
        assert event["repo"] == REPO_NAME
        assert event["branch"] == BRANCH
        assert event.get("event_id")
        assert event.get("ts")


def test_rag_exposes_retrieval_score_and_marks_changed_snapshot_stale() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        repo, snapshot = _make_git_repo(root)
        path = _kb_path(root)
        _write_jsonl(path, [_entry(repo=repo, snapshot=snapshot)])

        source = repo / "src" / "config.txt"
        source.write_text("version=2\n", encoding="utf-8")
        _git(repo, "add", "src/config.txt")
        _git(repo, "commit", "-m", "change snapshotted source")

        with (
            _isolated_kb(root, cwd=repo),
            patch.object(kb_search, "InvertedIndex", None),
            patch.object(kb_search, "get_inverted_index_path", None),
        ):
            rc, stdout, stderr = _call_main(
                kb_rag_context.main,
                [
                    "git evidence snapshot freshness sentinel",
                    "--repo",
                    REPO_NAME,
                    "--branch",
                    BRANCH,
                    "--limit",
                    "1",
                    "--json",
                    "--no-session-briefs",
                ],
            )

        assert rc == 0, stderr
        payload = json.loads(stdout)
        assert payload["hit_count"] == 1
        item = payload["items"][0]
        assert item["entry_id"] == ENTRY_ID
        assert isinstance(item.get("retrieval_score"), (int, float))
        assert item["confidence"] == item["retrieval_score"]
        assert item["freshness_state"] == "needs_recheck"
        assert str(item.get("warning", "")).strip()


def test_dirty_snapshot_blocks_heat_but_locate_is_recorded() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        repo, snapshot = _make_git_repo(root)
        path = _kb_path(root)
        _write_jsonl(path, [_entry(repo=repo, snapshot=snapshot)])
        original_bytes = path.read_bytes()

        # Keep the relevant file dirty: closeout must verify current Git state,
        # not trust the snapshot's historical worktree_state="clean" value.
        (repo / "src" / "config.txt").write_text("version=dirty\n", encoding="utf-8")

        with _isolated_kb(root, cwd=repo):
            for effect in ("decide", "fix", "write"):
                rc, _stdout, _stderr = _call_main(
                    kb_closeout.main,
                    [
                        "--query",
                        f"dirty evidence {effect}",
                        "--hit-count",
                        "1",
                        "--allowed-hit-id",
                        ENTRY_ID,
                        f"--used-{effect}",
                        ENTRY_ID,
                        "--repo",
                        REPO_NAME,
                        "--branch",
                        BRANCH,
                        "--session-id",
                        f"dirty-{effect}",
                    ],
                )
                assert rc != 0, f"dirty_worktree must block --used-{effect}"
                assert path.read_bytes() == original_bytes

            locate_rc, locate_stdout, locate_stderr = _call_main(
                kb_closeout.main,
                [
                    "--query",
                    "dirty evidence locate",
                    "--hit-count",
                    "1",
                    "--allowed-hit-id",
                    ENTRY_ID,
                    "--used-locate",
                    ENTRY_ID,
                    "--repo",
                    REPO_NAME,
                    "--branch",
                    BRANCH,
                    "--session-id",
                    "dirty-locate",
                ],
            )

        assert locate_rc == 0, locate_stderr or locate_stdout
        assert path.read_bytes() == original_bytes
        events = _read_raw_jsonl(_adoption_path(root))
        blocked_effects = {
            event.get("effect")
            for event in events
            if event.get("entry_id") == ENTRY_ID and event.get("effect") in {"decide", "fix", "write"}
        }
        assert blocked_effects == set()
        locate_events = [
            event
            for event in events
            if event.get("entry_id") == ENTRY_ID and event.get("effect") == "locate"
        ]
        assert len(locate_events) == 1
        assert locate_events[0].get("session_id") == "dirty-locate"


def test_closeout_retry_reuses_adoption_event_id() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        path = _kb_path(root)
        legacy_entry = _entry()
        legacy_entry.pop("evidence_snapshots", None)
        _write_jsonl(path, [legacy_entry])

        argv = [
            "--query", "idempotent closeout",
            "--hit-count", "1",
            "--allowed-hit-id", ENTRY_ID,
            "--used-fix", ENTRY_ID,
            "--repo", REPO_NAME,
            "--branch", BRANCH,
            "--closeout-id", "retry-safe-closeout",
        ]
        with _isolated_kb(root):
            first_rc, _first_out, first_err = _call_main(kb_closeout.main, argv)
            second_rc, _second_out, second_err = _call_main(kb_closeout.main, argv)

        assert first_rc == 0, first_err
        assert second_rc == 0, second_err
        events = _read_raw_jsonl(_adoption_path(root))
        matching = [event for event in events if event.get("entry_id") == ENTRY_ID]
        assert len(matching) == 2
        assert len({event.get("event_id") for event in matching}) == 1


def main() -> int:
    tests = [
        test_read_jsonl_rejects_git_conflict_markers,
        test_update_requires_matching_expected_rev_and_rotates_revision,
        test_update_use_appends_runtime_event_without_rewriting_durable_record,
        test_rag_exposes_retrieval_score_and_marks_changed_snapshot_stale,
        test_dirty_snapshot_blocks_heat_but_locate_is_recorded,
        test_closeout_retry_reuses_adoption_event_id,
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

    print("kb_git_safety tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
