"""Tests for repo-source North Star helpers."""

from __future__ import annotations

import subprocess
from datetime import UTC, datetime
from pathlib import Path

from guardian.models import NorthStar, Principle
from guardian.north_star import (
    read_repo_north_star,
    read_repo_north_star_markdown,
    write_repo_north_star,
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


def _sample(project_name: str) -> NorthStar:
    return NorthStar(
        version=1,
        project_name=project_name,
        identity_statement=f"{project_name} identity.",
        principles=[Principle(id="p1", rank=1, text="Keep the policy clear")],
        approved_architecture="Python package with GitHub Actions.",
        anti_patterns=[],
        created_at=datetime(2026, 5, 26, tzinfo=UTC),
    )


def test_write_repo_north_star_writes_docs_northstar(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()

    write_repo_north_star(repo, _sample("RepoPolicy"))

    assert (repo / "docs" / "northstar.md").exists()
    assert "RepoPolicy" in (repo / "docs" / "northstar.md").read_text(encoding="utf-8")


def test_read_repo_north_star_reads_working_tree_file(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    write_repo_north_star(repo, _sample("WorkingTreePolicy"))

    north_star = read_repo_north_star(repo)

    assert north_star.project_name == "WorkingTreePolicy"


def test_read_repo_north_star_markdown_can_read_git_ref_instead_of_checkout(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(["init", "-b", "main"], cwd=repo)
    _git(["config", "user.email", "test@example.com"], cwd=repo)
    _git(["config", "user.name", "Test"], cwd=repo)
    write_repo_north_star(repo, _sample("BasePolicy"))
    _git(["add", "docs/northstar.md"], cwd=repo)
    _git(["commit", "-m", "add base policy"], cwd=repo)

    write_repo_north_star(repo, _sample("HeadPolicy"))

    markdown = read_repo_north_star_markdown(repo, ref="HEAD")

    assert "BasePolicy" in markdown
    assert "HeadPolicy" not in markdown


def test_read_repo_north_star_markdown_rejects_flag_injection(tmp_path: Path) -> None:
    import pytest
    repo = tmp_path / "repo"
    repo.mkdir()

    with pytest.raises(ValueError, match="Invalid git ref"):
        read_repo_north_star_markdown(repo, ref="-V")

    with pytest.raises(ValueError, match="Invalid path"):
        read_repo_north_star_markdown(repo, ref="main", path="-o")
