from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from click.testing import CliRunner

from guardian.chronograph import (
    ActionClass,
    ChronographSafetyPolicy,
    ConfigDiff,
    RiskLevel,
    StewardshipAction,
    apply_plan,
    build_apply_plan,
    recommend_actions,
)
from guardian.cli import cli

NOW = datetime(2026, 5, 26, 12, 0, tzinfo=UTC)
_RTK_INCLUDE_LINE = "@" + str(Path.home() / ".codex" / "RTK.md")


def _make_rtk_repair_action(
    tmp_path: Path,
) -> tuple[Path, StewardshipAction, ChronographSafetyPolicy, list]:
    target = tmp_path / ".codex" / "AGENTS.md"
    target.parent.mkdir(exist_ok=True)
    before = "Always commit when feasible.\n"
    target.write_text(before, encoding="utf-8")
    action = StewardshipAction(
        id="repair-missing-rtk",
        action_class=ActionClass.REPAIR,
        target_path=str(target),
        before=before,
        after=before + _RTK_INCLUDE_LINE + "\n",
        reason="The diff showed AGENTS.md missing the durable RTK include.",
        confidence=0.96,
        metadata={"operation": "add_missing_include", "include": _RTK_INCLUDE_LINE},
    )
    policy = ChronographSafetyPolicy.for_repo(tmp_path)
    plan = build_apply_plan([action], policy=policy, now=NOW)
    return target, action, policy, plan


def test_recommend_actions_turns_missing_include_diff_into_repair_action(tmp_path: Path) -> None:
    target = tmp_path / ".codex" / "AGENTS.md"
    diff = ConfigDiff(
        target_path=str(target),
        before="Always commit when feasible.\n",
        after="Always commit when feasible.\n" + _RTK_INCLUDE_LINE + "\n",
        summary="missing include " + _RTK_INCLUDE_LINE,
        source="memory-curation",
        confidence=0.96,
    )

    actions = recommend_actions([diff])

    assert len(actions) == 1
    action = actions[0]
    assert action.action_class == ActionClass.REPAIR
    assert action.metadata["operation"] == "add_missing_include"
    assert action.confidence == pytest.approx(0.96)
    assert not action.destructive
    assert action.target_path == str(target)


def test_build_apply_plan_marks_high_confidence_additive_repair_auto_apply(tmp_path: Path) -> None:
    target = tmp_path / ".codex" / "AGENTS.md"
    target.parent.mkdir()
    target.write_text("Always commit when feasible.\n", encoding="utf-8")
    action = StewardshipAction(
        id="repair-missing-rtk",
        action_class=ActionClass.REPAIR,
        target_path=str(target),
        before="Always commit when feasible.\n",
        after="Always commit when feasible.\n" + _RTK_INCLUDE_LINE + "\n",
        reason="The diff showed AGENTS.md missing the durable RTK include.",
        confidence=0.96,
        metadata={"operation": "add_missing_include"},
    )
    policy = ChronographSafetyPolicy.for_repo(tmp_path)

    plan = build_apply_plan([action], policy=policy, now=NOW)

    assert len(plan) == 1
    item = plan[0]
    assert item.action_id == "repair-missing-rtk"
    assert item.risk_level == RiskLevel.LOW
    assert item.auto_apply
    assert str(tmp_path / ".github" / "guardian" / "chronograph" / "backups") in item.backup_path
    assert "@@ " in item.diff
    assert "Copy-Item" in item.rollback_command


def test_apply_plan_recomputes_auto_apply_from_source_action(tmp_path: Path) -> None:
    target = tmp_path / ".claude" / "agents" / "old.md"
    target.parent.mkdir(parents=True)
    before = "stale agent\n"
    target.write_text(before, encoding="utf-8")
    action = StewardshipAction(
        id="retire-old-agent",
        action_class=ActionClass.RETIRE,
        target_path=str(target),
        before=before,
        after="",
        reason="Timeline shows this agent is no longer referenced.",
        confidence=0.99,
        destructive=True,
        approved=False,
    )
    policy = ChronographSafetyPolicy.for_repo(tmp_path)
    plan = build_apply_plan([action], policy=policy, now=NOW)
    tampered = plan[0].model_copy(update={"auto_apply": True})

    results = apply_plan([tampered], policy=policy, actions=[action], now=NOW)

    assert not results[0].applied
    assert results[0].skipped_reason == "action requires explicit approval"
    assert target.read_text(encoding="utf-8") == before


def test_apply_plan_rejects_tampered_backup_path(tmp_path: Path) -> None:
    target, action, policy, plan = _make_rtk_repair_action(tmp_path)
    before = target.read_text(encoding="utf-8")
    tampered = plan[0].model_copy(update={"backup_path": str(tmp_path / "outside.bak")})

    results = apply_plan([tampered], policy=policy, actions=[action], now=NOW)

    assert not results[0].applied
    assert results[0].skipped_reason == "plan does not match source action"
    assert target.read_text(encoding="utf-8") == before
    assert not (tmp_path / "outside.bak").exists()


def test_apply_plan_rejects_tampered_target_existed(tmp_path: Path) -> None:
    target, action, policy, plan = _make_rtk_repair_action(tmp_path)
    before = target.read_text(encoding="utf-8")
    tampered = plan[0].model_copy(
        update={
            "target_existed": False,
            "rollback_command": f"Remove-Item -LiteralPath '{target.resolve()}' -Force",
        }
    )

    results = apply_plan([tampered], policy=policy, actions=[action], now=NOW)

    assert not results[0].applied
    assert results[0].skipped_reason == "plan does not match source action"
    assert target.read_text(encoding="utf-8") == before


def test_apply_plan_skips_when_live_target_changed_since_planning(tmp_path: Path) -> None:
    target, action, policy, plan = _make_rtk_repair_action(tmp_path)
    target.write_text("User changed this file after the plan.\n", encoding="utf-8")

    results = apply_plan(plan, policy=policy, actions=[action], now=NOW)

    assert not results[0].applied
    assert results[0].skipped_reason == "target changed since plan"
    assert target.read_text(encoding="utf-8") == "User changed this file after the plan.\n"


def test_approved_destructive_action_is_not_marked_auto_apply(tmp_path: Path) -> None:
    target = tmp_path / ".claude" / "agents" / "old.md"
    target.parent.mkdir(parents=True)
    target.write_text("stale agent\n", encoding="utf-8")
    action = StewardshipAction(
        id="retire-old-agent",
        action_class=ActionClass.RETIRE,
        target_path=str(target),
        before="stale agent\n",
        after="",
        reason="Timeline shows this agent is no longer referenced.",
        confidence=0.99,
        destructive=True,
        approved=True,
    )

    plan = build_apply_plan(
        [action],
        policy=ChronographSafetyPolicy.for_repo(tmp_path),
        now=NOW,
    )

    assert plan[0].approved
    assert not plan[0].auto_apply


def test_arbitrary_missing_include_is_not_auto_applied(tmp_path: Path) -> None:
    target = tmp_path / ".codex" / "AGENTS.md"
    target.parent.mkdir()
    before = "Always commit when feasible.\n"
    target.write_text(before, encoding="utf-8")
    diff = ConfigDiff(
        target_path=str(target),
        before=before,
        after=before + "@/tmp/UNREVIEWED.md\n",
        summary="missing include @/tmp/UNREVIEWED.md",
        source="memory-curation",
        confidence=0.96,
    )
    action = recommend_actions([diff])[0]

    plan = build_apply_plan(
        [action],
        policy=ChronographSafetyPolicy.for_repo(tmp_path),
        now=NOW,
    )

    assert action.action_class == ActionClass.REPAIR
    assert not plan[0].auto_apply


def test_metadata_cannot_claim_rtk_include_for_arbitrary_added_line(tmp_path: Path) -> None:
    target = tmp_path / ".codex" / "AGENTS.md"
    target.parent.mkdir()
    before = "Always commit when feasible.\n"
    target.write_text(before, encoding="utf-8")
    action = StewardshipAction(
        id="repair-metadata-lie",
        action_class=ActionClass.REPAIR,
        target_path=str(target),
        before=before,
        after=before + "@C:\\tmp\\UNREVIEWED.md\n",
        reason="The metadata claims this is an RTK include.",
        confidence=0.96,
        metadata={"operation": "add_missing_include", "include": _RTK_INCLUDE_LINE},
    )

    plan = build_apply_plan(
        [action],
        policy=ChronographSafetyPolicy.for_repo(tmp_path),
        now=NOW,
    )

    assert not plan[0].auto_apply


def test_non_canonical_rtk_include_path_is_not_auto_applied(tmp_path: Path) -> None:
    target = tmp_path / ".codex" / "AGENTS.md"
    target.parent.mkdir()
    before = "Always commit when feasible.\n"
    target.write_text(before, encoding="utf-8")
    action = StewardshipAction(
        id="repair-noncanonical-rtk",
        action_class=ActionClass.REPAIR,
        target_path=str(target),
        before=before,
        after=before + "@C:\\CustomIncludes\\RTK.md\n",
        reason="The diff adds an RTK-named include from an unknown path.",
        confidence=0.96,
        metadata={"operation": "add_missing_include", "include": "@C:\\CustomIncludes\\RTK.md"},
    )

    plan = build_apply_plan(
        [action],
        policy=ChronographSafetyPolicy.for_repo(tmp_path),
        now=NOW,
    )

    assert not plan[0].auto_apply


def test_only_missing_rtk_include_operation_auto_applies(tmp_path: Path) -> None:
    target = tmp_path / ".codex" / "settings.json"
    target.parent.mkdir()
    before = "{}\n"
    target.write_text(before, encoding="utf-8")
    action = StewardshipAction(
        id="normalize-setting",
        action_class=ActionClass.REPAIR,
        target_path=str(target),
        before=before,
        after='{"safe": true}\n',
        reason="Known-safe settings normalization.",
        confidence=0.99,
        metadata={"operation": "normalize_known_safe_setting"},
    )

    plan = build_apply_plan(
        [action],
        policy=ChronographSafetyPolicy.for_repo(tmp_path),
        now=NOW,
    )

    assert not plan[0].auto_apply


def test_missing_include_does_not_auto_apply_to_settings_json(tmp_path: Path) -> None:
    target = tmp_path / ".codex" / "settings.json"
    target.parent.mkdir()
    before = "{}\n"
    target.write_text(before, encoding="utf-8")
    action = StewardshipAction(
        id="bad-settings-include",
        action_class=ActionClass.REPAIR,
        target_path=str(target),
        before=before,
        after=before + _RTK_INCLUDE_LINE + "\n",
        reason="A setting file cannot safely accept markdown include syntax.",
        confidence=0.96,
        metadata={"operation": "add_missing_include", "include": _RTK_INCLUDE_LINE},
    )

    plan = build_apply_plan(
        [action],
        policy=ChronographSafetyPolicy.for_repo(tmp_path),
        now=NOW,
    )

    assert not plan[0].auto_apply


def test_new_file_apply_plan_uses_remove_item_rollback(tmp_path: Path) -> None:
    target = tmp_path / ".codex" / "AGENTS.md"
    action = StewardshipAction(
        id="create-agents",
        action_class=ActionClass.REPAIR,
        target_path=str(target),
        before="",
        after=_RTK_INCLUDE_LINE + "\n",
        reason="The diff showed AGENTS.md missing the durable RTK include.",
        confidence=0.96,
        metadata={"operation": "add_missing_include", "include": _RTK_INCLUDE_LINE},
    )

    plan = build_apply_plan(
        [action],
        policy=ChronographSafetyPolicy.for_repo(tmp_path),
        now=NOW,
    )

    assert plan[0].target_existed is False
    assert "Remove-Item" in plan[0].rollback_command


def test_sqlite_state_files_are_forbidden_by_suffix(tmp_path: Path) -> None:
    target = tmp_path / ".codex" / "memory" / "state.sqlite3"
    action = StewardshipAction(
        id="bad-sqlite-write",
        action_class=ActionClass.REPAIR,
        target_path=str(target),
        before="",
        after="sqlite bytes",
        reason="Should never be allowed.",
        confidence=1.0,
    )

    with pytest.raises(ValueError, match="forbidden"):
        build_apply_plan([action], policy=ChronographSafetyPolicy.for_repo(tmp_path), now=NOW)


def test_sqlite_sidecar_files_are_forbidden(tmp_path: Path) -> None:
    target = tmp_path / ".codex" / "memory" / "state.sqlite-wal"
    action = StewardshipAction(
        id="bad-sqlite-sidecar-write",
        action_class=ActionClass.REPAIR,
        target_path=str(target),
        before="",
        after="sqlite wal bytes",
        reason="Should never be allowed.",
        confidence=1.0,
    )

    with pytest.raises(ValueError, match="forbidden"):
        build_apply_plan([action], policy=ChronographSafetyPolicy.for_repo(tmp_path), now=NOW)


def test_build_apply_plan_allows_home_level_tool_roots(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    target = home / ".codex" / "AGENTS.md"
    target.parent.mkdir(parents=True)
    target.write_text("Always commit when feasible.\n", encoding="utf-8")
    monkeypatch.setattr("guardian.chronograph.Path.home", staticmethod(lambda: home))
    rtk_include = f"@{(home / '.codex' / 'RTK.md').as_posix()}"
    action = StewardshipAction(
        id="repair-home-root",
        action_class=ActionClass.REPAIR,
        target_path=str(target),
        before="Always commit when feasible.\n",
        after=f"Always commit when feasible.\n{rtk_include}\n",
        reason="The diff showed a home-level Codex include was missing.",
        confidence=0.96,
        metadata={"operation": "add_missing_include"},
    )

    plan = build_apply_plan(
        [action],
        policy=ChronographSafetyPolicy.for_repo(tmp_path / "repo"),
        now=NOW,
    )

    assert plan[0].auto_apply
    assert plan[0].target_path == str(target.resolve())


def test_apply_plan_creates_backup_and_audit_for_live_write(tmp_path: Path) -> None:
    target = tmp_path / ".codex" / "AGENTS.md"
    target.parent.mkdir()
    before = "Always commit when feasible.\n"
    after = before + _RTK_INCLUDE_LINE + "\n"
    target.write_text(before, encoding="utf-8")
    action = StewardshipAction(
        id="repair-missing-rtk",
        action_class=ActionClass.REPAIR,
        target_path=str(target),
        before=before,
        after=after,
        reason="The diff showed AGENTS.md missing the durable RTK include.",
        confidence=0.96,
        metadata={"operation": "add_missing_include"},
    )
    policy = ChronographSafetyPolicy.for_repo(tmp_path)
    plan = build_apply_plan([action], policy=policy, now=NOW)

    results = apply_plan(plan, policy=policy, actions=[action], now=NOW)

    assert target.read_text(encoding="utf-8") == after
    assert len(results) == 1
    result = results[0]
    assert result.applied
    assert result.backup_path is not None
    assert Path(result.backup_path).read_text(encoding="utf-8") == before
    audit_path = tmp_path / ".github" / "guardian" / "chronograph" / "audit.jsonl"
    audit = [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines()]
    assert audit[-1]["action_id"] == "repair-missing-rtk"
    assert audit[-1]["old_hash"] == result.old_hash
    assert audit[-1]["new_hash"] == result.new_hash
    assert audit[-1]["backup_path"] == result.backup_path


def test_forbidden_paths_are_rejected_before_planning(tmp_path: Path) -> None:
    token_file = tmp_path / ".codex" / "auth.json"
    token_file.parent.mkdir()
    token_file.write_text('{"token":"secret"}', encoding="utf-8")
    action = StewardshipAction(
        id="bad-token-write",
        action_class=ActionClass.REPAIR,
        target_path=str(token_file),
        before='{"token":"secret"}',
        after='{"token":"other"}',
        reason="Should never be allowed.",
        confidence=1.0,
    )

    with pytest.raises(ValueError, match="forbidden"):
        build_apply_plan([action], policy=ChronographSafetyPolicy.for_repo(tmp_path), now=NOW)


def test_destructive_retire_action_is_not_auto_applied(tmp_path: Path) -> None:
    target = tmp_path / ".claude" / "agents" / "old.md"
    target.parent.mkdir(parents=True)
    target.write_text("stale agent\n", encoding="utf-8")
    action = StewardshipAction(
        id="retire-old-agent",
        action_class=ActionClass.RETIRE,
        target_path=str(target),
        before="stale agent\n",
        after="",
        reason="Timeline shows this agent is no longer referenced.",
        confidence=0.99,
        destructive=True,
        approved=False,
    )

    plan = build_apply_plan(
        [action],
        policy=ChronographSafetyPolicy.for_repo(tmp_path),
        now=NOW,
    )
    results = apply_plan(
        plan,
        policy=ChronographSafetyPolicy.for_repo(tmp_path),
        actions=[action],
        now=NOW,
    )

    assert plan[0].risk_level == RiskLevel.HIGH
    assert not plan[0].auto_apply
    assert not results[0].applied
    assert target.read_text(encoding="utf-8") == "stale agent\n"


def test_chronograph_cli_writes_recommendations_plan_and_audit(tmp_path: Path) -> None:
    runner = CliRunner()
    target = tmp_path / ".codex" / "AGENTS.md"
    target.parent.mkdir()
    target.write_text("Always commit when feasible.\n", encoding="utf-8")
    chronograph_dir = tmp_path / ".github" / "guardian" / "chronograph"
    chronograph_dir.mkdir(parents=True)
    diff = ConfigDiff(
        target_path=str(target),
        before=target.read_text(encoding="utf-8"),
        after="Always commit when feasible.\n" + _RTK_INCLUDE_LINE + "\n",
        summary="missing include " + _RTK_INCLUDE_LINE,
        source="memory-curation",
        confidence=0.96,
    )
    (chronograph_dir / "diffs.json").write_text(
        json.dumps([diff.model_dump(mode="json")], indent=2),
        encoding="utf-8",
    )

    recommend = runner.invoke(
        cli,
        ["chronograph-recommend-actions", "--repo-root", str(tmp_path)],
    )
    plan = runner.invoke(
        cli,
        ["chronograph-plan-apply", "--repo-root", str(tmp_path)],
    )
    applied = runner.invoke(
        cli,
        ["chronograph-apply", "--repo-root", str(tmp_path)],
    )

    assert recommend.exit_code == 0, recommend.output
    assert plan.exit_code == 0, plan.output
    assert applied.exit_code == 0, applied.output
    assert (chronograph_dir / "recommendations.json").exists()
    assert (chronograph_dir / "apply-plan.json").exists()
    assert (chronograph_dir / "audit.jsonl").exists()
    assert _RTK_INCLUDE_LINE in target.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Additional coverage tests
# ---------------------------------------------------------------------------


def test_validate_target_relative_path_resolves_under_chronograph_root(
    tmp_path: Path,
) -> None:
    """A relative path that resolves under the chronograph root is accepted."""
    policy = ChronographSafetyPolicy.for_repo(tmp_path)
    # The chronograph root is tmp_path/.guardian/chronograph; create a subdir
    target_abs = policy.chronograph_root / "subdir" / "file.json"
    target_abs.parent.mkdir(parents=True, exist_ok=True)
    # Pass the relative form from repo root
    rel = target_abs.relative_to(tmp_path)
    resolved = policy.validate_target(str(rel))
    assert resolved == target_abs.resolve()


def test_validate_target_raises_outside_writable_roots(tmp_path: Path) -> None:
    """A path outside all writable roots raises ValueError."""
    policy = ChronographSafetyPolicy.for_repo(tmp_path)
    outside = tmp_path.parent / "outside" / "file.txt"
    outside.parent.mkdir(parents=True, exist_ok=True)
    outside.write_text("x", encoding="utf-8")
    with pytest.raises(ValueError, match="outside writable roots"):
        policy.validate_target(str(outside))


def test_validate_target_live_root_non_allowlisted_raises(tmp_path: Path) -> None:
    """A path under a live root that is not allowlisted raises ValueError."""
    policy = ChronographSafetyPolicy.for_repo(tmp_path)
    # live_roots includes ~/.codex; create a non-allowlisted file there
    from pathlib import Path as P

    codex_dir = P.home() / ".codex"
    codex_dir.mkdir(exist_ok=True)
    not_allowed = codex_dir / "not_allowed.bin"
    not_allowed.write_bytes(b"x")
    try:
        with pytest.raises(ValueError, match="not allowlisted"):
            policy.validate_target(str(not_allowed))
    finally:
        not_allowed.unlink(missing_ok=True)


def test_recommend_actions_destructive_diff_produces_retire_action(
    tmp_path: Path,
) -> None:
    """A diff where after is empty produces a RETIRE action."""
    target = tmp_path / ".codex" / "AGENTS.md"
    diff = ConfigDiff(
        target_path=str(target),
        before="Some config content.\n",
        after="",  # empty after → destructive
        summary="Config removed.",
        source="memory-curation",
        confidence=0.7,
    )
    actions = recommend_actions([diff])
    assert len(actions) == 1
    assert actions[0].action_class == ActionClass.RETIRE


def test_unique_id_deduplication_appends_suffix() -> None:
    """_unique_id appends -2, -3 … when the base slug is already used."""
    from guardian.chronograph import _unique_id

    used: set[str] = set()
    id1 = _unique_id("my-action", used)
    id2 = _unique_id("my-action", used)
    id3 = _unique_id("my-action", used)
    assert id1 == "my-action"
    assert id2 == "my-action-2"
    assert id3 == "my-action-3"


def test_detect_missing_include_falls_back_to_summary_regex(tmp_path: Path) -> None:
    """When no @ line is added, _detect_missing_include reads the summary."""
    from guardian.chronograph import _detect_missing_include

    include = "@" + str(Path.home() / ".codex" / "rtk.md")
    result = _detect_missing_include(
        before="same content",
        after="same content",  # no new @ lines
        summary=f"missing include {include}",
    )
    assert result == include


def test_apply_plan_source_action_missing_is_skipped(tmp_path: Path) -> None:
    """An apply plan item whose action_id is not in actions list is skipped."""
    target, action, policy, plan = _make_rtk_repair_action(tmp_path)
    # Pass empty actions list so the action_id lookup fails
    results = apply_plan(plan, actions=[], policy=policy, now=NOW)
    assert len(results) == 1
    assert not results[0].applied
    assert "missing" in (results[0].skipped_reason or "").lower()


def test_apply_plan_new_file_creates_sentinel_backup(tmp_path: Path) -> None:
    """When the target doesn't exist, apply_plan creates an empty sentinel backup."""
    target = tmp_path / ".codex" / "AGENTS.md"
    policy = ChronographSafetyPolicy.for_repo(tmp_path)
    before = ""  # target doesn't exist yet
    after = _RTK_INCLUDE_LINE + "\n"  # only add the known-safe include
    action = StewardshipAction(
        id="repair-new",
        action_class=ActionClass.REPAIR,
        target_path=str(target),
        before=before,
        after=after,
        reason="New AGENTS.md with RTK include.",
        confidence=0.96,
        metadata={"operation": "add_missing_include", "include": _RTK_INCLUDE_LINE},
    )
    plan = build_apply_plan([action], policy=policy, now=NOW)
    assert len(plan) == 1
    assert plan[0].auto_apply is True
    results = apply_plan(plan, actions=[action], policy=policy, now=NOW)
    assert len(results) == 1
    assert results[0].applied
    backup = Path(results[0].backup_path)
    assert backup.exists()
    assert backup.read_text(encoding="utf-8") == ""


def test_apply_plan_oserror_is_caught(tmp_path: Path) -> None:
    """An OSError during write is caught and reported without crashing."""
    import unittest.mock as mock

    target, action, policy, plan = _make_rtk_repair_action(tmp_path)
    with mock.patch("pathlib.Path.write_text", side_effect=OSError("disk full")):
        results = apply_plan(plan, actions=[action], policy=policy, now=NOW)
    assert len(results) == 1
    result = results[0]
    assert not result.applied
    assert result.error is not None
    assert "disk full" in result.error


def test_load_model_list_missing_file_returns_empty(tmp_path: Path) -> None:
    """_load_model_list returns [] when the file doesn't exist."""
    from guardian.chronograph import StewardshipAction, _load_model_list

    missing = tmp_path / "nonexistent.json"
    assert _load_model_list(missing, StewardshipAction) == []


def test_can_auto_apply_returns_false_for_propose_action(tmp_path: Path) -> None:
    """_can_auto_apply returns False for PROPOSE actions."""
    from guardian.chronograph import _can_auto_apply

    target = tmp_path / ".codex" / "AGENTS.md"
    action = StewardshipAction(
        id="propose-1",
        action_class=ActionClass.PROPOSE,
        target_path=str(target),
        before="",
        after="new content",
        reason="Proposed change.",
        confidence=0.95,
        metadata={"operation": "add_missing_include"},
    )
    policy = ChronographSafetyPolicy.for_repo(tmp_path)
    assert not _can_auto_apply(action, policy)


def test_adds_only_known_safe_include_empty_added_lines_returns_false() -> None:
    """_adds_only_known_safe_include returns False when no lines are added."""
    from guardian.chronograph import _adds_only_known_safe_include

    result = _adds_only_known_safe_include("identical", "identical")
    assert result is False


def test_added_lines_returns_empty_when_before_has_extra_lines() -> None:
    """_added_lines returns [] when before has lines absent from after."""
    from guardian.chronograph import _added_lines

    result = _added_lines(before="line1\nline2\nline3", after="line1\nline3")
    assert result == []


def test_restore_after_failure_removes_new_file(tmp_path: Path) -> None:
    """_restore_after_failure removes a newly created target when it did not exist."""
    from guardian.chronograph import _restore_after_failure

    target = tmp_path / "new_file.txt"
    target.write_text("written by apply", encoding="utf-8")
    backup = tmp_path / "backup.bak"
    _restore_after_failure(target, backup, existed=False)
    assert not target.exists()


def test_restore_after_failure_restores_from_backup(tmp_path: Path) -> None:
    """_restore_after_failure copies backup back when target existed."""
    from guardian.chronograph import _restore_after_failure

    target = tmp_path / "target.txt"
    target.write_text("corrupted", encoding="utf-8")
    backup = tmp_path / "backup.bak"
    backup.write_text("original", encoding="utf-8")
    _restore_after_failure(target, backup, existed=True)
    assert target.read_text(encoding="utf-8") == "original"


def test_is_forbidden_path_returns_true_for_forbidden_part(tmp_path: Path) -> None:
    """_is_forbidden_path returns True when a part matches a forbidden directory."""
    from guardian.chronograph import _is_forbidden_path

    parts = ["home", "user", ".cache", "some_app", "file.json"]
    assert _is_forbidden_path([p.lower() for p in parts], "file.json") is True


def test_is_allowed_live_file_returns_false_for_unknown_extension(
    tmp_path: Path,
) -> None:
    """_is_allowed_live_file returns False for an unlisted file type."""
    from guardian.chronograph import _is_allowed_live_file

    target = tmp_path / "somefile.xyz"
    assert not _is_allowed_live_file(target)


def _make_rtk_repair_action_changed(
    tmp_path: Path,
    *,
    change: str = "target_path",
) -> tuple[StewardshipAction, ChronographSafetyPolicy, list]:
    """Build a valid plan but return a modified action to trigger _plan_action_mismatch."""
    target, action, policy, plan = _make_rtk_repair_action(tmp_path)
    if change == "target_path":
        other_target = tmp_path / ".codex" / "claude.md"
        other_target.write_text(action.before, encoding="utf-8")
        modified = action.model_copy(update={"target_path": str(other_target)})
    elif change == "action_class":
        modified = action.model_copy(update={"action_class": ActionClass.PROPOSE})
    elif change == "hashes":
        modified = action.model_copy(update={"before": "tampered-before-content\n"})
    elif change == "destructive":
        modified = action.model_copy(update={"destructive": True})
    else:
        modified = action
    return modified, policy, plan


def test_apply_plan_mismatch_target_path_is_skipped(tmp_path: Path) -> None:
    """_plan_action_mismatch skips when action.target_path != plan.target_path."""
    modified, policy, plan = _make_rtk_repair_action_changed(tmp_path, change="target_path")
    results = apply_plan(plan, actions=[modified], policy=policy, now=NOW)
    assert len(results) == 1
    assert not results[0].applied
    assert results[0].skipped_reason is not None


def test_apply_plan_mismatch_action_class_is_skipped(tmp_path: Path) -> None:
    """_plan_action_mismatch skips when action.action_class != plan.action_class."""
    modified, policy, plan = _make_rtk_repair_action_changed(tmp_path, change="action_class")
    results = apply_plan(plan, actions=[modified], policy=policy, now=NOW)
    assert len(results) == 1
    assert not results[0].applied
    assert results[0].skipped_reason is not None


def test_apply_plan_mismatch_hashes_is_skipped(tmp_path: Path) -> None:
    """_plan_action_mismatch skips when action hashes don't match plan hashes."""
    modified, policy, plan = _make_rtk_repair_action_changed(tmp_path, change="hashes")
    results = apply_plan(plan, actions=[modified], policy=policy, now=NOW)
    assert len(results) == 1
    assert not results[0].applied
    assert results[0].skipped_reason is not None


def test_apply_plan_mismatch_destructive_flag_is_skipped(tmp_path: Path) -> None:
    """_plan_action_mismatch skips when action.destructive != plan.destructive."""
    modified, policy, plan = _make_rtk_repair_action_changed(tmp_path, change="destructive")
    results = apply_plan(plan, actions=[modified], policy=policy, now=NOW)
    assert len(results) == 1
    assert not results[0].applied
    assert results[0].skipped_reason is not None


def test_apply_plan_mismatch_backup_path_is_skipped(tmp_path: Path) -> None:
    """_plan_action_mismatch skips when backup_path doesn't match expected."""
    target, action, policy, plan = _make_rtk_repair_action(tmp_path)
    # Change backup_path to a valid-looking path under backup_root but wrong action ID
    original = Path(plan[0].backup_path)
    # Replace the action-id segment (3rd component from backup_root) with 'wrong-id'
    wrong_backup = (
        policy.backup_root
        / original.relative_to(policy.backup_root).parts[0]  # timestamp
        / "wrong-action-id"
        / "AGENTS.md"
    )
    tampered = [plan[0].model_copy(update={"backup_path": str(wrong_backup)})]
    results = apply_plan(tampered, actions=[action], policy=policy, now=NOW)
    assert len(results) == 1
    assert not results[0].applied
    assert results[0].skipped_reason is not None


def test_apply_plan_mismatch_rollback_command_is_skipped(tmp_path: Path) -> None:
    """_plan_action_mismatch skips when rollback_command doesn't match expected."""
    target, action, policy, plan = _make_rtk_repair_action(tmp_path)
    tampered = [plan[0].model_copy(update={"rollback_command": "echo tampered"})]
    results = apply_plan(tampered, actions=[action], policy=policy, now=NOW)
    assert len(results) == 1
    assert not results[0].applied
    assert results[0].skipped_reason is not None
