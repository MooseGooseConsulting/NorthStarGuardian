"""Tests for guardian.github_io."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from github.PullRequest import PullRequest
from github.Repository import Repository

from guardian.github_io import GitHubContext, get_pr_diff, get_pr_meta, post_pr_comment


class TestGitHubContextFromEnv:
    def test_pull_request_event(self, tmp_path: Path) -> None:
        """from_env correctly parses a pull_request event."""
        payload = {
            "pull_request": {
                "number": 42,
                "title": "Test PR",
                "body": "Test body",
                "user": {"login": "testuser"},
                "base": {"sha": "abc123", "ref": "main"},
                "head": {"sha": "def456", "ref": "feature"},
                "html_url": "https://github.com/test/repo/pull/42",
            }
        }
        event_file = tmp_path / "event.json"
        event_file.write_text(json.dumps(payload), encoding="utf-8")

        mock_repo = MagicMock(spec=Repository)
        mock_pr = MagicMock(spec=PullRequest)
        mock_pr.number = 42
        mock_repo.get_pull.return_value = mock_pr

        with (
            patch(
                "guardian.github_io.os.environ",
                {
                    "GITHUB_TOKEN": "test-token",
                    "GITHUB_REPOSITORY": "test/repo",
                    "GITHUB_EVENT_NAME": "pull_request",
                },
            ),
            patch("guardian.github_io.Github") as mock_gh_cls,
        ):
            mock_gh = mock_gh_cls.return_value
            mock_gh.get_repo.return_value = mock_repo

            ctx = GitHubContext.from_env(event_path=str(event_file))

        assert ctx.event_name == "pull_request"
        assert ctx.pr is not None
        assert ctx.pr.number == 42

    def test_issue_comment_event_is_ignored(self, tmp_path: Path) -> None:
        """from_env ignores issue_comment events; Guardian has no command surface."""
        payload = {
            "comment": {"body": "/status"},
            "issue": {"number": 42},
        }
        event_file = tmp_path / "event.json"
        event_file.write_text(json.dumps(payload), encoding="utf-8")

        mock_repo = MagicMock(spec=Repository)
        mock_pr = MagicMock(spec=PullRequest)
        mock_pr.number = 42
        mock_repo.get_pull.return_value = mock_pr

        with (
            patch(
                "guardian.github_io.os.environ",
                {
                    "GITHUB_TOKEN": "test-token",
                    "GITHUB_REPOSITORY": "test/repo",
                    "GITHUB_EVENT_NAME": "issue_comment",
                },
            ),
            patch("guardian.github_io.Github") as mock_gh_cls,
        ):
            mock_gh = mock_gh_cls.return_value
            mock_gh.get_repo.return_value = mock_repo

            ctx = GitHubContext.from_env(event_path=str(event_file))

        assert ctx.event_name == "issue_comment"
        assert ctx.pr is None
        mock_repo.get_pull.assert_not_called()

    def test_no_pr_in_event(self, tmp_path: Path) -> None:
        """from_env returns pr=None when no PR is in the event."""
        payload = {"action": "created"}
        event_file = tmp_path / "event.json"
        event_file.write_text(json.dumps(payload), encoding="utf-8")

        with (
            patch(
                "guardian.github_io.os.environ",
                {
                    "GITHUB_TOKEN": "test-token",
                    "GITHUB_REPOSITORY": "test/repo",
                    "GITHUB_EVENT_NAME": "pull_request",
                },
            ),
            patch("guardian.github_io.Github"),
        ):
            ctx = GitHubContext.from_env(event_path=str(event_file))

        assert ctx.pr is None


class TestGetPrDiff:
    def test_reconstructs_unified_diff(self) -> None:
        """get_pr_diff reconstructs a unified diff from PR file objects."""
        mock_pr = MagicMock(spec=PullRequest)
        mock_file1 = MagicMock()
        mock_file1.filename = "src/main.py"
        mock_file1.patch = "@@ -1 +1 @@\n-old\n+new"
        mock_file2 = MagicMock()
        mock_file2.filename = "src/util.py"
        mock_file2.patch = "@@ -0,0 +1 @@\n+newfile"
        mock_pr.get_files.return_value = [mock_file1, mock_file2]

        mock_ctx = MagicMock()
        mock_ctx.pr = mock_pr

        result = get_pr_diff(mock_ctx)

        assert "src/main.py" in result
        assert "src/util.py" in result
        assert "diff --git" in result

    def test_binary_file_no_patch(self) -> None:
        """Binary files without a patch are noted."""
        mock_pr = MagicMock(spec=PullRequest)
        mock_file = MagicMock()
        mock_file.filename = "image.png"
        mock_file.patch = None
        mock_pr.get_files.return_value = [mock_file]

        mock_ctx = MagicMock()
        mock_ctx.pr = mock_pr

        result = get_pr_diff(mock_ctx)

        assert "binary or no patch" in result

    def test_no_pr_raises(self) -> None:
        """get_pr_diff raises RuntimeError when ctx.pr is None."""
        mock_ctx = MagicMock()
        mock_ctx.pr = None

        with pytest.raises(RuntimeError, match="no PR in context"):
            get_pr_diff(mock_ctx)


class TestGetPrMeta:
    def test_extracts_metadata(self) -> None:
        """get_pr_meta extracts the expected metadata dict."""
        mock_pr = MagicMock(spec=PullRequest)
        mock_pr.number = 42
        mock_pr.title = "Test PR"
        mock_pr.body = "Body text"
        mock_pr.user.login = "testuser"
        mock_pr.base.sha = "abc123"
        mock_pr.head.sha = "def456"
        mock_pr.base.ref = "main"
        mock_pr.head.ref = "feature"
        mock_pr.html_url = "https://github.com/test/repo/pull/42"

        mock_ctx = MagicMock()
        mock_ctx.pr = mock_pr

        meta = get_pr_meta(mock_ctx)

        assert meta["number"] == 42
        assert meta["title"] == "Test PR"
        assert meta["author"] == "testuser"
        assert meta["base_sha"] == "abc123"
        assert meta["head_sha"] == "def456"

    def test_no_pr_raises(self) -> None:
        """get_pr_meta raises RuntimeError when ctx.pr is None."""
        mock_ctx = MagicMock()
        mock_ctx.pr = None

        with pytest.raises(RuntimeError, match="no PR in context"):
            get_pr_meta(mock_ctx)


class TestPostPrComment:
    def test_posts_comment(self) -> None:
        """post_pr_comment posts a comment on the PR."""
        mock_pr = MagicMock(spec=PullRequest)
        mock_ctx = MagicMock()
        mock_ctx.pr = mock_pr

        post_pr_comment(mock_ctx, "Test comment body")

        mock_pr.create_issue_comment.assert_called_once_with("Test comment body")

    def test_no_pr_raises(self) -> None:
        """post_pr_comment raises RuntimeError when ctx.pr is None."""
        mock_ctx = MagicMock()
        mock_ctx.pr = None

        with pytest.raises(RuntimeError, match="no PR in context"):
            post_pr_comment(mock_ctx, "test")


class TestGitHubContextNoEventPath:
    def test_no_event_path_uses_empty_payload(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When GITHUB_EVENT_PATH is absent, context uses an empty payload."""
        from unittest.mock import MagicMock, patch

        from guardian.github_io import GitHubContext

        monkeypatch.setenv("GITHUB_TOKEN", "fake-token")
        monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repo")
        monkeypatch.setenv("GITHUB_EVENT_NAME", "push")
        monkeypatch.delenv("GITHUB_EVENT_PATH", raising=False)

        mock_gh = MagicMock()
        mock_repo = MagicMock()
        mock_gh.get_repo.return_value = mock_repo

        with patch("guardian.github_io.Github", return_value=mock_gh):
            ctx = GitHubContext.from_env(event_path=None)

        assert ctx.pr is None
        assert ctx.repo is mock_repo
