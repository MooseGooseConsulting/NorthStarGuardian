"""Tests for guardian.memory repo-native storage."""

from __future__ import annotations

from pathlib import Path

import pytest

from guardian.memory import MemoryNotInitialized, MemoryStore


def _make_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    (path / "README.md").write_text("# test repo\n", encoding="utf-8")
    return path


class TestEnsureInitialized:
    def test_creates_guardian_directory_in_normal_checkout(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path / "repo")
        store = MemoryStore(repo)

        store.ensure_initialized()

        assert (repo / ".github" / "guardian").is_dir()

    def test_idempotent_second_call(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path / "repo")
        store = MemoryStore(repo)

        store.ensure_initialized()
        store.ensure_initialized()

        assert (repo / ".github" / "guardian").is_dir()


class TestReadGuard:
    def test_read_raises_before_initialize(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path / "repo")
        store = MemoryStore(repo)

        with pytest.raises(MemoryNotInitialized):
            store.read("anything.txt")

    def test_exists_raises_before_initialize(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path / "repo")
        store = MemoryStore(repo)

        with pytest.raises(MemoryNotInitialized):
            store.exists("anything.txt")


class TestReadWrite:
    def _initialized_store(self, tmp_path: Path) -> tuple[Path, MemoryStore]:
        repo = _make_repo(tmp_path / "repo")
        store = MemoryStore(repo)
        store.ensure_initialized()
        return repo, store

    def test_write_then_read_roundtrip(self, tmp_path: Path) -> None:
        repo, store = self._initialized_store(tmp_path)

        store.write("memory/journal/entry.md", "# Entry\nHello.")

        assert store.read("memory/journal/entry.md") == "# Entry\nHello."
        assert (repo / ".github" / "guardian" / "memory" / "journal" / "entry.md").exists()

    def test_read_missing_raises(self, tmp_path: Path) -> None:
        _repo, store = self._initialized_store(tmp_path)

        with pytest.raises(FileNotFoundError):
            store.read("does-not-exist.txt")

    def test_exists_true_and_false(self, tmp_path: Path) -> None:
        _repo, store = self._initialized_store(tmp_path)

        store.write("present.txt", "yes")

        assert store.exists("present.txt") is True
        assert store.exists("absent.txt") is False

    def test_write_json_and_read_json(self, tmp_path: Path) -> None:
        _repo, store = self._initialized_store(tmp_path)
        obj = {"key": "value", "count": 42}

        store.write_json("data.json", obj)

        assert store.read_json("data.json") == obj

    def test_list_empty_prefix(self, tmp_path: Path) -> None:
        _repo, store = self._initialized_store(tmp_path)

        store.write("a.txt", "a")
        store.write("subdir/b.txt", "b")

        assert store.list() == ["a.txt", "subdir/b.txt"]

    def test_list_with_prefix(self, tmp_path: Path) -> None:
        _repo, store = self._initialized_store(tmp_path)

        store.write("memory/journal/2026-01-01.md", "j1")
        store.write("memory/journal/2026-01-02.md", "j2")
        store.write("memory/sagas/overhaul.md", "s1")

        journal_files = store.list("memory/journal")

        assert all(f.startswith("memory/journal/") for f in journal_files)
        assert len(journal_files) == 2

    def test_list_missing_prefix_returns_empty(self, tmp_path: Path) -> None:
        _repo, store = self._initialized_store(tmp_path)

        assert store.list("nonexistent-dir") == []

    def test_rejects_path_traversal(self, tmp_path: Path) -> None:
        _repo, store = self._initialized_store(tmp_path)

        with pytest.raises(ValueError, match="outside Guardian storage"):
            store.write("../escape.txt", "no")

    def test_rejects_absolute_path(self, tmp_path: Path) -> None:
        _repo, store = self._initialized_store(tmp_path)

        with pytest.raises(ValueError, match="must be relative"):
            store.write("/etc/passwd", "no")

    def test_list_with_file_prefix_returns_that_file(self, tmp_path: Path) -> None:
        _repo, store = self._initialized_store(tmp_path)
        store.write("memory/journal/entry.md", "content")

        result = store.list("memory/journal/entry.md")

        assert result == ["memory/journal/entry.md"]

    def test_custom_relative_storage_path(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path / "repo")
        # Relative storage_path should be resolved under repo_root
        store = MemoryStore(repo, storage_path=Path("custom/guardian"))
        store.ensure_initialized()

        store.write("file.txt", "hello")

        assert store.read("file.txt") == "hello"
        assert (repo / "custom" / "guardian" / "file.txt").exists()


class TestCommitAndPush:
    def test_commit_and_push_is_noop_for_repo_native_store(self, tmp_path: Path) -> None:
        _repo, store = TestReadWrite()._initialized_store(tmp_path)

        store.write("note.txt", "persistent")
        store.commit_and_push("add note", push=True)

        assert store.read("note.txt") == "persistent"

    def test_empty_commit_is_noop(self, tmp_path: Path) -> None:
        _repo, store = TestReadWrite()._initialized_store(tmp_path)

        store.commit_and_push("empty commit", push=False)


class TestSession:
    def test_session_yields_same_store(self, tmp_path: Path) -> None:
        _repo, store = TestReadWrite()._initialized_store(tmp_path)

        with store.session("check yield", push=False) as s:
            assert s is store

    def test_session_keeps_written_files(self, tmp_path: Path) -> None:
        _repo, store = TestReadWrite()._initialized_store(tmp_path)

        with store.session("batch write", push=False) as s:
            s.write("a.txt", "alpha")
            s.write("b.txt", "beta")

        assert store.read("a.txt") == "alpha"
        assert store.read("b.txt") == "beta"

    def test_commit_and_push_calls_subprocess_when_staged(self, tmp_path: Path) -> None:
        """commit_and_push calls git commit when staged changes exist."""
        from unittest.mock import MagicMock, patch

        repo, store = TestReadWrite()._initialized_store(tmp_path)

        def fake_run(args, **kwargs):
            result = MagicMock()
            if "diff" in args:
                result.returncode = 1  # staged changes exist
            else:
                result.returncode = 0
            return result

        with patch("guardian.memory.subprocess.run", side_effect=fake_run) as mock_run:
            store.commit_and_push("test commit", push=True)

        assert any("add" in str(c) for c in mock_run.call_args_list)
        assert any("commit" in str(c) for c in mock_run.call_args_list)
        assert any("push" in str(c) for c in mock_run.call_args_list)

    def test_commit_no_push_skips_push(self, tmp_path: Path) -> None:
        """commit_and_push skips git push when push=False."""
        from unittest.mock import MagicMock, patch

        repo, store = TestReadWrite()._initialized_store(tmp_path)

        def fake_run(args, **kwargs):
            result = MagicMock()
            if "diff" in args:
                result.returncode = 1  # staged changes exist
            else:
                result.returncode = 0
            return result

        with patch("guardian.memory.subprocess.run", side_effect=fake_run) as mock_run:
            store.commit_and_push("commit only", push=False)

        all_args = [c.args[0] for c in mock_run.call_args_list]
        # "push" should not appear as a git subcommand (index 3)
        assert not any(len(a) > 3 and a[3] == "push" for a in all_args)
        assert any(len(a) > 3 and a[3] == "commit" for a in all_args)
