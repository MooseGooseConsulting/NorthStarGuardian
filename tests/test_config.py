"""Tests for Guardian runtime configuration models."""

from __future__ import annotations

from guardian.models import GuardianConfig


def test_guardian_config_defaults_to_repo_north_star_source() -> None:
    config = GuardianConfig()

    assert config.north_star.source == "repo"
    assert config.north_star.repo_path == "docs/northstar.md"
    assert config.north_star.active_copy_path == ".github/guardian/northstar.md"


def test_guardian_config_has_linear_section() -> None:
    config = GuardianConfig(
        north_star={"source": "linear"},
        linear={"document_id": "doc-1", "team_id": "team-1"},
    )

    assert config.north_star.source == "linear"
    assert config.linear.document_id == "doc-1"
    assert config.linear.team_id == "team-1"


def test_guardian_config_migrates_legacy_flat_source_settings() -> None:
    config = GuardianConfig(
        north_star_source="linear",
        repo_north_star_path="docs/policy.md",
        active_north_star_path=".github/guardian/policy/current.md",
        linear_document_id="doc-1",
        linear_team_id="team-1",
    )

    assert config.north_star.source == "linear"
    assert config.north_star.repo_path == "docs/policy.md"
    assert config.north_star.active_copy_path == ".github/guardian/policy/current.md"
    assert config.linear.document_id == "doc-1"
    assert config.linear.team_id == "team-1"

    flattened = GuardianConfig(
        source="linear",
        repo_path="docs/legacy-policy.md",
        active_copy_path=".github/guardian/legacy.md",
        document_id="doc-2",
        team_id="team-2",
    )
    assert flattened.north_star.source == "linear"
    assert flattened.north_star.repo_path == "docs/legacy-policy.md"
    assert flattened.north_star.active_copy_path == ".github/guardian/legacy.md"
    assert flattened.linear.document_id == "doc-2"
    assert flattened.linear.team_id == "team-2"


def test_guardian_config_new_nested_values_override_legacy_aliases() -> None:
    config = GuardianConfig(
        north_star={"source": "repo", "repo_path": "docs/current.md"},
        north_star_source="linear",
        repo_north_star_path="docs/legacy.md",
        linear={"document_id": "new-doc"},
        linear_document_id="legacy-doc",
    )

    assert config.north_star.source == "repo"
    assert config.north_star.repo_path == "docs/current.md"
    assert config.linear.document_id == "new-doc"


def test_guardian_config_no_longer_exposes_legacy_storage_branch_field() -> None:
    config = GuardianConfig()

    assert not hasattr(config, "memory" + "_branch")
