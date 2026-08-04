#!/usr/bin/env python3
from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

import kb_evidence


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


def _repo(root: Path) -> Path:
    repo = root / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.name", "KB Evidence Test")
    _git(repo, "config", "user.email", "kb-evidence@example.invalid")
    _git(repo, "remote", "add", "origin", "https://user:secret@example.invalid/acme/demo.git")
    source = repo / "src" / "config.txt"
    source.parent.mkdir()
    source.write_text("version=1\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "initial")
    return repo


def _entry(repo: Path) -> dict:
    entry = {
        "id": "entry-1",
        "workspace_dir": str(repo),
        "source_paths": ["src/config.txt"],
        "title": "evidence",
        "used_count": 2,
        "last_used_ts": "old",
    }
    entry["evidence_snapshots"] = kb_evidence.capture_evidence_snapshots(entry, repo)
    return entry


def test_record_revision_ignores_legacy_heat_only_fields() -> None:
    base = {"id": "a", "title": "same", "used_count": 1, "last_used_ts": "old"}
    changed_heat = {**base, "used_count": 99, "last_used_ts": "new"}
    changed_content = {**base, "title": "changed"}
    assert kb_evidence.canonical_entry_revision(base) == kb_evidence.canonical_entry_revision(changed_heat)
    assert kb_evidence.canonical_entry_revision(base) != kb_evidence.canonical_entry_revision(changed_content)


def test_git_blob_stays_fresh_after_unrelated_commit_then_needs_recheck() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        repo = _repo(Path(temp_dir))
        entry = _entry(repo)
        snapshot = entry["evidence_snapshots"][0]
        assert snapshot["type"] == "git_file"
        assert snapshot["repo_id"].startswith("sha256:")
        assert "secret" not in str(snapshot)
        assert kb_evidence.verify_entry_evidence(entry)["state"] == "fresh"

        (repo / "README.md").write_text("unrelated\n", encoding="utf-8")
        _git(repo, "add", "README.md")
        _git(repo, "commit", "-m", "unrelated")
        assert kb_evidence.verify_entry_evidence(entry)["state"] == "fresh"

        source = repo / "src" / "config.txt"
        source.write_text("version=2\n", encoding="utf-8")
        _git(repo, "add", "src/config.txt")
        _git(repo, "commit", "-m", "change evidence")
        result = kb_evidence.verify_entry_evidence(entry)
        assert result["state"] == "needs_recheck", result


def test_dirty_worktree_and_diverged_history_are_not_fresh() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        repo = _repo(Path(temp_dir))
        entry = _entry(repo)
        initial = _git(repo, "rev-parse", "HEAD")
        source = repo / "src" / "config.txt"
        source.write_text("dirty\n", encoding="utf-8")
        assert kb_evidence.verify_entry_evidence(entry)["state"] == "dirty_worktree"

        _git(repo, "restore", "src/config.txt")
        _git(repo, "checkout", "--orphan", "rewritten")
        _git(repo, "rm", "-rf", ".")
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text("version=1\n", encoding="utf-8")
        _git(repo, "add", ".")
        _git(repo, "commit", "-m", "rewritten root")
        assert _git(repo, "rev-parse", "HEAD") != initial
        result = kb_evidence.verify_entry_evidence(entry)
        assert result["state"] == "diverged", result


def test_plain_file_hash_and_legacy_states() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        workspace = Path(temp_dir)
        source = workspace / "note.txt"
        source.write_text("one", encoding="utf-8")
        entry = {"workspace_dir": str(workspace), "source_paths": ["note.txt"]}
        entry["evidence_snapshots"] = kb_evidence.capture_evidence_snapshots(entry, workspace)
        assert kb_evidence.verify_entry_evidence(entry)["state"] == "fresh"
        source.write_text("two", encoding="utf-8")
        assert kb_evidence.verify_entry_evidence(entry)["state"] == "needs_recheck"
        assert kb_evidence.verify_entry_evidence({})["state"] == "legacy_unverified"
        assert kb_evidence.verify_entry_evidence({"evidence_snapshots": []})["state"] == "not_snapshotted"


def main() -> int:
    tests = [
        test_record_revision_ignores_legacy_heat_only_fields,
        test_git_blob_stays_fresh_after_unrelated_commit_then_needs_recheck,
        test_dirty_worktree_and_diverged_history_are_not_fresh,
        test_plain_file_hash_and_legacy_states,
    ]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print("kb_evidence tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
