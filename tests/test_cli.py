"""Tests for guardian.cli — parse_slash_command and CLI subcommands.

Uses Click's CliRunner so no real processes are spawned.  All external
dependencies (GitHub, OpenAI, MemoryStore) are mocked at the boundary.
"""

from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from guardian.cli import cli, parse_slash_command
from guardian.models import (
    DiffAnalysis,
    GuardianConfig,
    IntentSummary,
    InterviewReport,
    JournalEntry,
    NorthStar,
    Principle,
    Verdict,
)

# ---------------------------------------------------------------------------
# parse_slash_command
# ---------------------------------------------------------------------------


class TestParseSlashCommand:
    def test_simple_command(self) -> None:
        result = parse_slash_command("/init-guardian")
        assert result is not None
        cmd, args = result
        assert cmd == "init-guardian"
        assert args == []

    def test_command_with_single_arg(self) -> None:
        result = parse_slash_command("/amend principle-3")
        assert result is not None
        cmd, args = result
        assert cmd == "amend"
        assert args == ["principle-3"]

    def test_command_with_quoted_second_arg(self) -> None:
        result = parse_slash_command('/amend principle-3 "new text here"')
        assert result is not None
        cmd, args = result
        assert cmd == "amend"
        assert args == ["principle-3", "new text here"]

    def test_command_with_unquoted_rest(self) -> None:
        result = parse_slash_command("/amend principle-3 new text")
        assert result is not None
        cmd, args = result
        assert cmd == "amend"
        # Unquoted: first word + rest as second token
        assert args[0] == "principle-3"
        assert args[1] == "new text"

    def test_re_anchor(self) -> None:
        result = parse_slash_command("/re-anchor")
        assert result is not None
        assert result[0] == "re-anchor"
        assert result[1] == []

    def test_chronicle(self) -> None:
        result = parse_slash_command("/chronicle")
        assert result is not None
        assert result[0] == "chronicle"

    def test_dashboard(self) -> None:
        result = parse_slash_command("/dashboard")
        assert result is not None
        assert result[0] == "dashboard"

    def test_status(self) -> None:
        result = parse_slash_command("/status")
        assert result is not None
        assert result[0] == "status"

    def test_non_slash_comment_returns_none(self) -> None:
        assert parse_slash_command("not a command") is None

    def test_empty_string_returns_none(self) -> None:
        assert parse_slash_command("") is None

    def test_plain_text_with_slash_in_middle_returns_none(self) -> None:
        assert parse_slash_command("hello /not-a-command") is None

    def test_leading_whitespace_is_stripped(self) -> None:
        result = parse_slash_command("  /status  ")
        assert result is not None
        assert result[0] == "status"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_north_star() -> NorthStar:
    return NorthStar(
        version=1,
        project_name="TestProject",
        identity_statement="This is a test project.",
        principles=[
            Principle(id="p1", rank=1, text="Principle one"),
            Principle(id="p2", rank=2, text="Principle two"),
        ],
        approved_architecture="We use pytest for testing.",
        anti_patterns=[],
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


def _make_report(pr_number: int = 42) -> InterviewReport:
    return InterviewReport(
        pr_number=pr_number,
        overall_verdict=Verdict.ALIGNED,
        alignment_summary="This PR aligns with the project identity.",
        principle_evaluations=[],
        anti_pattern_matches=[],
        saga_id="test-saga",
        suggestions=["Consider adding a docstring."],
        chronicle_paragraph="PR #42 extended the test suite.",
        intent=IntentSummary(one_line="Add tests", paragraph="Adds unit tests."),
        created_at=datetime(2026, 1, 2, tzinfo=UTC),
    )


def _make_journal_entry(pr_number: int = 42) -> JournalEntry:
    return JournalEntry(
        pr_number=pr_number,
        timestamp=datetime(2026, 1, 2, tzinfo=UTC),
        saga_id="test-saga",
        verdict=Verdict.ALIGNED,
        body_markdown="PR #42 extended the test suite.",
    )


def _git(args: list[str], cwd: Path) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _init_git_repo(path: Path) -> None:
    _git(["init", "-b", "main"], cwd=path)
    _git(["config", "user.email", "test@example.com"], cwd=path)
    _git(["config", "user.name", "Test"], cwd=path)


# ---------------------------------------------------------------------------
# Review North Star loading
# ---------------------------------------------------------------------------


class TestReviewNorthStarLoading:
    def test_repo_source_reads_base_ref_and_updates_active_copy(self, tmp_path: Path) -> None:
        from guardian.cli import _load_review_north_star
        from guardian.memory import MemoryStore
        from guardian.north_star import write_repo_north_star

        repo = tmp_path / "repo"
        repo.mkdir()
        _init_git_repo(repo)

        base = _make_north_star().model_copy(update={"project_name": "BasePolicy"})
        head = _make_north_star().model_copy(update={"project_name": "HeadPolicy"})
        write_repo_north_star(repo, base)
        _git(["add", "docs/northstar.md"], cwd=repo)
        _git(["commit", "-m", "add base north star"], cwd=repo)
        write_repo_north_star(repo, head)

        store = MemoryStore(repo)
        store.ensure_initialized()

        north_star = _load_review_north_star(
            repo,
            store,
            GuardianConfig(),
            {"base_sha": "HEAD"},
        )

        assert north_star.project_name == "BasePolicy"
        assert "BasePolicy" in store.read("northstar.md")
        assert store.read_json("memory/northstar-snapshot.json")["source"] == "repo"

    def test_linear_source_requires_api_key(self, tmp_path: Path) -> None:
        from click import ClickException

        from guardian.cli import _load_review_north_star
        from guardian.memory import MemoryStore

        store = MemoryStore(tmp_path)
        store.ensure_initialized()
        config = GuardianConfig(
            north_star={"source": "linear"},
            linear={"document_id": "doc-1"},
        )

        with pytest.raises(ClickException, match="LINEAR_API_KEY"):
            _load_review_north_star(tmp_path, store, config, {}, env={})

    def test_linear_source_fetches_document_and_writes_snapshot(
        self,
        tmp_path: Path,
    ) -> None:
        from guardian.cli import _load_review_north_star
        from guardian.linear import LinearDocumentSnapshot
        from guardian.memory import MemoryStore

        store = MemoryStore(tmp_path)
        store.ensure_initialized()
        config = GuardianConfig(
            north_star={"source": "linear"},
            linear={"document_id": "doc-1"},
        )
        content = _make_north_star().model_copy(update={"project_name": "LinearPolicy"})

        fake_client = MagicMock()
        from guardian.north_star import render_north_star_markdown

        fake_client.fetch_document.return_value = LinearDocumentSnapshot(
            document_id="doc-1",
            document_content_id="content-1",
            title="Linear North Star",
            url="https://linear.app/acme/document/doc-1",
            updated_at="2026-05-26T12:00:00.000Z",
            updated_by="Ada",
            fetched_at=datetime(2026, 5, 26, tzinfo=UTC),
            sha256="a" * 64,
            content=render_north_star_markdown(content),
        )

        with patch("guardian.linear.LinearClient", return_value=fake_client):
            north_star = _load_review_north_star(
                tmp_path,
                store,
                config,
                {},
                env={"LINEAR_API_KEY": "lin-test"},
            )

        assert north_star.project_name == "LinearPolicy"
        assert "LinearPolicy" in store.read("northstar.md")
        assert store.read_json("memory/northstar-snapshot.json")["document_id"] == "doc-1"


# ---------------------------------------------------------------------------
# guardian preview-dashboard
# ---------------------------------------------------------------------------


class TestPreviewDashboard:
    """preview-dashboard renders an HTML file without touching git."""

    def test_renders_html_file(self, tmp_path: Path) -> None:
        runner = CliRunner()

        north_star = _make_north_star()
        html_content = "<html><body>dashboard</body></html>"
        output_file = tmp_path / "out.html"

        mock_store = MagicMock()
        mock_store.exists.return_value = False

        import guardian.dashboard as dash_mod

        orig_render = dash_mod.render_dashboard
        dash_mod.render_dashboard = MagicMock(return_value=html_content)

        try:
            with (
                patch("guardian.cli.MemoryStore", return_value=mock_store),
                patch("guardian.cli.read_north_star", return_value=north_star),
            ):
                result = runner.invoke(
                    cli,
                    [
                        "preview-dashboard",
                        "--output",
                        str(output_file),
                        "--repo-root",
                        str(tmp_path),
                    ],
                )
        finally:
            dash_mod.render_dashboard = orig_render

        assert result.exit_code == 0, result.output
        # The output path should be mentioned in stdout.
        assert str(output_file.name) in result.output or "dashboard" in result.output.lower()

    def test_errors_when_no_north_star(self, tmp_path: Path) -> None:
        runner = CliRunner()

        mock_store = MagicMock()
        mock_store.exists.return_value = False

        with (
            patch("guardian.cli.MemoryStore", return_value=mock_store),
            patch("guardian.cli.read_north_star", side_effect=FileNotFoundError),
        ):
            result = runner.invoke(
                cli,
                [
                    "preview-dashboard",
                    "--output",
                    str(tmp_path / "out.html"),
                    "--repo-root",
                    str(tmp_path),
                ],
            )

        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# guardian interview (mocked at GitHubContext boundary)
# ---------------------------------------------------------------------------


class TestInterview:
    """interview invokes the right pipeline given a mocked event payload."""

    def _make_event_payload(self, pr_number: int = 42) -> dict[str, Any]:
        return {
            "pull_request": {
                "number": pr_number,
                "title": "Add feature",
                "body": "Adds a new feature",
                "user": {"login": "developer"},
                "base": {"sha": "abc123", "ref": "main"},
                "head": {"sha": "def456", "ref": "feature-branch"},
                "html_url": f"https://github.com/test/repo/pull/{pr_number}",
            }
        }

    def test_interview_pipeline_called(self, tmp_path: Path) -> None:
        runner = CliRunner()

        # Write a fake event payload to disk.
        payload = self._make_event_payload(42)
        event_file = tmp_path / "event.json"
        event_file.write_text(json.dumps(payload), encoding="utf-8")

        north_star = _make_north_star()
        report = _make_report(42)

        mock_store = MagicMock()
        mock_store.exists.return_value = False
        mock_store.__enter__ = MagicMock(return_value=mock_store)
        mock_store.__exit__ = MagicMock(return_value=False)
        mock_store.session = MagicMock(return_value=mock_store)
        mock_store.session.return_value.__enter__ = MagicMock(return_value=mock_store)
        mock_store.session.return_value.__exit__ = MagicMock(return_value=False)

        mock_pr = MagicMock()
        mock_pr.number = 42

        mock_ctx = MagicMock()
        mock_ctx.pr = mock_pr
        mock_ctx.event_name = "pull_request"

        mock_diff_analysis = MagicMock(spec=DiffAnalysis)

        env = {
            "GITHUB_TOKEN": "test-token",
            "GITHUB_REPOSITORY": "test/repo",
            "GITHUB_EVENT_NAME": "pull_request",
            "OPENAI_API_KEY": "test-key",
        }

        with (
            patch("guardian.cli.MemoryStore", return_value=mock_store),
            patch("guardian.cli.GitHubContext") as mock_ctx_cls,
            patch("guardian.cli.get_pr_diff", return_value="diff --git ..."),
            patch(
                "guardian.cli.get_pr_meta",
                return_value={
                    "number": 42,
                    "title": "Add feature",
                    "body": "",
                    "author": "dev",
                    "base_sha": "abc",
                    "head_sha": "def",
                },
            ),
            patch("guardian.cli._load_review_north_star", return_value=north_star),
            patch("guardian.cli._make_openai_client", return_value=MagicMock()),
            patch("guardian.cli._load_config", return_value=GuardianConfig()),
            patch("guardian.cli.post_pr_comment") as mock_post,
        ):
            mock_ctx_cls.from_env.return_value = mock_ctx

            # Patch domain modules loaded inside the command.
            import guardian.analyze as analyze_mod
            import guardian.chronicle as chronicle_mod
            import guardian.dashboard as dashboard_mod

            mock_saga = MagicMock()
            mock_saga.id = "test-saga"

            with (
                patch.object(analyze_mod, "analyze_diff", return_value=mock_diff_analysis),
                patch.object(analyze_mod, "run_interview", return_value=report),
                patch.object(chronicle_mod, "_load_saga_index", return_value={"sagas": []}),
                patch.object(chronicle_mod, "_saga_from_index_entry", return_value=mock_saga),
                patch.object(chronicle_mod, "assign_saga", return_value=mock_saga),
                patch.object(chronicle_mod, "update_saga", return_value=mock_saga),
                patch.object(chronicle_mod, "write_journal_entry"),
                patch.object(dashboard_mod, "render_dashboard", return_value="<html/>"),
            ):
                result = runner.invoke(
                    cli,
                    ["interview", "--event-path", str(event_file), "--repo-root", str(tmp_path)],
                    env=env,
                )

        assert result.exit_code == 0, result.output
        mock_post.assert_called_once()
        # The comment body should mention the PR number.
        comment_body = mock_post.call_args[0][1]
        assert "42" in comment_body

    def test_interview_fails_without_pr(self, tmp_path: Path) -> None:
        runner = CliRunner()

        payload = {"action": "created"}
        event_file = tmp_path / "event.json"
        event_file.write_text(json.dumps(payload), encoding="utf-8")

        mock_store = MagicMock()

        mock_ctx = MagicMock()
        mock_ctx.pr = None  # No PR in context.

        env = {
            "GITHUB_TOKEN": "test-token",
            "GITHUB_REPOSITORY": "test/repo",
            "GITHUB_EVENT_NAME": "issue_comment",
            "OPENAI_API_KEY": "test-key",
        }

        with (
            patch("guardian.cli.MemoryStore", return_value=mock_store),
            patch("guardian.cli.GitHubContext") as mock_ctx_cls,
            patch("guardian.cli._make_openai_client", return_value=MagicMock()),
            patch("guardian.cli._load_config", return_value=GuardianConfig()),
        ):
            mock_ctx_cls.from_env.return_value = mock_ctx

            result = runner.invoke(
                cli,
                ["interview", "--event-path", str(event_file), "--repo-root", str(tmp_path)],
                env=env,
            )

        assert result.exit_code != 0
