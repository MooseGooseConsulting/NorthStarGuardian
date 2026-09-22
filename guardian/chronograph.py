"""Chronograph stewardship pipeline.

Chronograph turns curated configuration history into bounded, auditable
maintenance actions. It is deliberately not a blind sync engine: every live
write is allowlisted, backed up, hashed, and recorded in an append-only audit
log under ``.github/guardian/chronograph``.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import re
import shutil
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel, Field

_CHRONOGRAPH_ROOT = ".github/guardian/chronograph"
_AUDIT_PATH = f"{_CHRONOGRAPH_ROOT}/audit.jsonl"
_BACKUP_ROOT = f"{_CHRONOGRAPH_ROOT}/backups"
_M = TypeVar("_M", bound=BaseModel)


class ActionClass(StrEnum):
    """Stewardship action categories."""

    OBSERVE = "observe"
    PROPOSE = "propose"
    PROMOTE = "promote"
    REPAIR = "repair"
    RETIRE = "retire"


class RiskLevel(StrEnum):
    """Apply risk bucket."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ConfigDiff(BaseModel):
    """Curated configuration change evidence from earlier Chronograph stages."""

    target_path: str
    before: str
    after: str
    summary: str
    source: str
    confidence: float = 0.0
    narrative_ref: str | None = None


class StewardshipAction(BaseModel):
    """Concrete action Chronograph may propose or apply."""

    id: str
    action_class: ActionClass
    target_path: str
    before: str
    after: str
    reason: str
    confidence: float
    destructive: bool = False
    approved: bool = False
    source: str | None = None
    narrative_ref: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ApplyPlanItem(BaseModel):
    """Auditable apply plan for one stewardship action."""

    action_id: str
    action_class: ActionClass
    target_path: str
    before: str
    after: str
    diff: str
    risk_level: RiskLevel
    auto_apply: bool
    backup_path: str
    rollback_command: str
    target_existed: bool
    reason: str
    confidence: float
    old_hash: str
    new_hash: str
    destructive: bool = False
    approved: bool = False
    narrative_ref: str | None = None


class ApplyResult(BaseModel):
    """Result of attempting one apply plan item."""

    action_id: str
    target_path: str
    applied: bool
    skipped_reason: str | None = None
    backup_path: str | None = None
    old_hash: str
    new_hash: str
    rollback_command: str
    error: str | None = None


class ChronographSafetyPolicy(BaseModel):
    """Allowlist and denylist for live configuration writes."""

    repo_root: Path
    live_roots: list[Path]

    model_config = {"arbitrary_types_allowed": True}

    @classmethod
    def for_repo(cls, repo_root: Path) -> ChronographSafetyPolicy:
        root = repo_root.resolve()
        home = Path.home().resolve()
        return cls(
            repo_root=root,
            live_roots=[
                root / ".claude",
                root / ".codex",
                root / ".gemini",
                root / ".opencode",
                root / "opencode",
                home / ".claude",
                home / ".codex",
                home / ".gemini",
                home / ".opencode",
                home / "AppData" / "Roaming" / "opencode",
            ],
        )

    @property
    def chronograph_root(self) -> Path:
        return self.repo_root / _CHRONOGRAPH_ROOT

    @property
    def backup_root(self) -> Path:
        return self.repo_root / _BACKUP_ROOT

    @property
    def audit_path(self) -> Path:
        return self.repo_root / _AUDIT_PATH

    def resolve_target(self, target_path: str) -> Path:
        candidate = Path(target_path)
        if not candidate.is_absolute():
            candidate = self.repo_root / candidate
        return candidate.resolve()

    def validate_target(self, target_path: str) -> Path:
        target = self.resolve_target(target_path)
        lowered_parts = [p.lower() for p in target.parts]
        lowered_name = target.name.lower()

        if _is_forbidden_path(lowered_parts, lowered_name):
            raise ValueError(f"Chronograph target is forbidden: {target}")

        if _is_under(target, self.chronograph_root):
            return target

        if any(_is_under(target, root.resolve()) for root in self.live_roots):
            if _is_allowed_live_file(target):
                return target
            raise ValueError(f"Chronograph target is not allowlisted: {target}")

        raise ValueError(f"Chronograph target is outside writable roots: {target}")


def recommend_actions(diffs: list[ConfigDiff]) -> list[StewardshipAction]:
    """Turn curated diffs into concrete stewardship actions."""
    actions: list[StewardshipAction] = []
    used_ids: set[str] = set()

    for diff in diffs:
        action_class = ActionClass.PROPOSE
        operation = "write_recommendation"
        destructive = _looks_destructive(diff.before, diff.after)

        missing_include = _detect_missing_include(diff.before, diff.after, diff.summary)
        if missing_include:
            action_class = ActionClass.REPAIR
            operation = "add_missing_include"
            destructive = False
        elif destructive:
            action_class = ActionClass.RETIRE
            operation = "retire_stale_config"

        action_id = _unique_id(
            f"{action_class.value}-{Path(diff.target_path).stem}-{operation}",
            used_ids,
        )
        actions.append(
            StewardshipAction(
                id=action_id,
                action_class=action_class,
                target_path=diff.target_path,
                before=diff.before,
                after=diff.after,
                reason=_action_reason(diff, operation),
                confidence=diff.confidence,
                destructive=destructive,
                source=diff.source,
                narrative_ref=diff.narrative_ref,
                metadata={
                    "operation": operation,
                    **({"include": missing_include} if missing_include else {}),
                },
            )
        )

    return actions


def build_apply_plan(
    actions: list[StewardshipAction],
    *,
    policy: ChronographSafetyPolicy,
    now: datetime | None = None,
) -> list[ApplyPlanItem]:
    """Create apply plan items with target, diff, risk, backup, and rollback."""
    timestamp = _timestamp(now)
    plan: list[ApplyPlanItem] = []

    for action in actions:
        target = policy.validate_target(action.target_path)
        target_existed = target.exists()
        backup_path = _backup_path(policy, target, action.id, timestamp)
        item = ApplyPlanItem(
            action_id=action.id,
            action_class=action.action_class,
            target_path=str(target),
            before=action.before,
            after=action.after,
            diff=_unified_diff(action.before, action.after, str(target)),
            risk_level=_risk_for(action),
            auto_apply=_can_auto_apply(action, policy),
            backup_path=str(backup_path),
            rollback_command=_rollback_command(backup_path, target, target_existed),
            target_existed=target_existed,
            reason=action.reason,
            confidence=action.confidence,
            old_hash=_sha256(action.before),
            new_hash=_sha256(action.after),
            destructive=action.destructive,
            approved=action.approved,
            narrative_ref=action.narrative_ref,
        )
        plan.append(item)

    return plan


def apply_plan(
    plan: list[ApplyPlanItem],
    *,
    policy: ChronographSafetyPolicy,
    actions: list[StewardshipAction],
    now: datetime | None = None,
) -> list[ApplyResult]:
    """Apply approved or auto-apply plan items and append audit entries."""
    action_by_id = {action.id: action for action in actions}
    results: list[ApplyResult] = []

    for item in plan:
        target = policy.validate_target(item.target_path)
        action = action_by_id.get(item.action_id)
        if action is None:
            result = _skip_result(item, target, "source action missing")
            _append_audit(policy, item, result, now=now)
            results.append(result)
            continue

        mismatch = _plan_action_mismatch(item, action, policy)
        if mismatch:
            result = _skip_result(item, target, mismatch)
            _append_audit(policy, item, result, now=now)
            results.append(result)
            continue

        if not (_can_auto_apply(action, policy) or action.approved):
            result = _skip_result(item, target, "action requires explicit approval")
            _append_audit(policy, item, result, now=now)
            results.append(result)
            continue

        current = target.read_text(encoding="utf-8") if target.exists() else ""
        if _sha256(current) != item.old_hash:
            result = _skip_result(item, target, "target changed since plan")
            _append_audit(policy, item, result, now=now)
            results.append(result)
            continue

        backup = Path(item.backup_path)
        existed = target.exists()
        try:
            backup.parent.mkdir(parents=True, exist_ok=True)
            if existed:
                shutil.copy2(target, backup)
            else:
                backup.write_text("", encoding="utf-8")

            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(action.after, encoding="utf-8")
            result = ApplyResult(
                action_id=item.action_id,
                target_path=str(target),
                applied=True,
                backup_path=str(backup),
                old_hash=item.old_hash,
                new_hash=item.new_hash,
                rollback_command=item.rollback_command,
            )
            _append_audit(policy, item, result, now=now)
            results.append(result)
        except (OSError, shutil.Error) as exc:
            _restore_after_failure(target, backup, existed)
            result = ApplyResult(
                action_id=item.action_id,
                target_path=str(target),
                applied=False,
                skipped_reason="apply failed",
                backup_path=str(backup) if backup.exists() else None,
                old_hash=item.old_hash,
                new_hash=item.new_hash,
                rollback_command=item.rollback_command,
                error=str(exc),
            )
            _append_audit(policy, item, result, now=now)
            results.append(result)

    return results


def _load_model_list(path: Path, model_cls: type[_M]) -> list[_M]:
    if not path.exists():
        return []
    return [model_cls(**item) for item in json.loads(path.read_text(encoding="utf-8"))]


def load_diffs(path: Path) -> list[ConfigDiff]:
    return _load_model_list(path, ConfigDiff)


def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    serializable = (
        [item.model_dump(mode="json") if isinstance(item, BaseModel) else item for item in obj]
        if isinstance(obj, list)
        else (obj.model_dump(mode="json") if isinstance(obj, BaseModel) else obj)
    )
    path.write_text(json.dumps(serializable, indent=2), encoding="utf-8")


def load_actions(path: Path) -> list[StewardshipAction]:
    return _load_model_list(path, StewardshipAction)


def load_apply_plan(path: Path) -> list[ApplyPlanItem]:
    return _load_model_list(path, ApplyPlanItem)


def chronograph_path(repo_root: Path, name: str) -> Path:
    return repo_root.resolve() / _CHRONOGRAPH_ROOT / name


def _skip_result(item: ApplyPlanItem, target: Path, reason: str) -> ApplyResult:
    return ApplyResult(
        action_id=item.action_id,
        target_path=str(target),
        applied=False,
        skipped_reason=reason,
        old_hash=item.old_hash,
        new_hash=item.new_hash,
        rollback_command=item.rollback_command,
    )


def _detect_missing_include(before: str, after: str, summary: str) -> str | None:
    before_lines = set(before.splitlines())
    for line in after.splitlines():
        stripped = line.strip()
        if stripped.startswith("@") and stripped not in before_lines:
            return stripped
    match = re.search(r"missing include\s+(@\S+)", summary, flags=re.IGNORECASE)
    return match.group(1) if match else None


def _action_reason(diff: ConfigDiff, operation: str) -> str:
    return (
        f"{diff.summary}. Chronograph classified this as {operation} from "
        f"{diff.source} with confidence {diff.confidence:.2f}."
    )


def _looks_destructive(before: str, after: str) -> bool:
    return bool(before.strip()) and not after.strip()


def _unique_id(base: str, used: set[str]) -> str:
    slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", base.lower()).strip("-") or "action"
    candidate = slug
    suffix = 2
    while candidate in used:
        candidate = f"{slug}-{suffix}"
        suffix += 1
    used.add(candidate)
    return candidate


def _risk_for(action: StewardshipAction) -> RiskLevel:
    if action.destructive or action.action_class == ActionClass.RETIRE:
        return RiskLevel.HIGH
    if (
        action.action_class == ActionClass.REPAIR
        and action.confidence >= 0.9
        and action.metadata.get("operation") == "add_missing_include"
    ):
        return RiskLevel.LOW
    return RiskLevel.MEDIUM


def _can_auto_apply(action: StewardshipAction, policy: ChronographSafetyPolicy) -> bool:
    if action.destructive or action.action_class == ActionClass.RETIRE:
        return False
    if action.action_class != ActionClass.REPAIR:
        return False

    if action.confidence < 0.9 or action.metadata.get("operation") != "add_missing_include":
        return False

    target = policy.resolve_target(action.target_path)
    if not _is_instruction_include_file(target):
        return False

    if action.metadata.get("operation") == "add_missing_include":
        include = str(
            action.metadata.get("include")
            or _detect_missing_include(action.before, action.after, "")
        )
        if not _is_known_safe_include(include):
            return False
        if not _adds_only_known_safe_include(action.before, action.after):
            return False

    return True


def _unified_diff(before: str, after: str, target_path: str) -> str:
    before_lines = before.splitlines(keepends=True)
    after_lines = after.splitlines(keepends=True)
    return "".join(
        difflib.unified_diff(
            before_lines,
            after_lines,
            fromfile=f"{target_path} (before)",
            tofile=f"{target_path} (after)",
        )
    )


def _plan_action_mismatch(
    item: ApplyPlanItem,
    action: StewardshipAction,
    policy: ChronographSafetyPolicy,
) -> str | None:
    if policy.resolve_target(action.target_path) != policy.resolve_target(item.target_path):
        return "plan does not match source action"
    if item.action_class != action.action_class:
        return "plan does not match source action"
    if item.old_hash != _sha256(action.before) or item.new_hash != _sha256(action.after):
        return "plan does not match source action"
    if item.destructive != action.destructive or item.approved != action.approved:
        return "plan does not match source action"
    timestamp = _extract_backup_timestamp(item.backup_path, policy)
    if timestamp is None:
        return "plan does not match source action"
    target = policy.resolve_target(action.target_path)
    target_existed = target.exists()
    if item.target_existed != target_existed:
        return "plan does not match source action"
    expected_backup = _backup_path(policy, target, action.id, timestamp)
    expected_rollback = _rollback_command(expected_backup, target, target_existed)
    if Path(item.backup_path).resolve() != expected_backup.resolve():
        return "plan does not match source action"
    if item.rollback_command != expected_rollback:
        return "plan does not match source action"
    return None


def _is_known_safe_include(include: str) -> bool:
    normalized = include.strip().lstrip("@").replace("\\", "/").lower()
    home_posix = Path.home().as_posix().lower()
    return normalized == f"{home_posix}/.codex/rtk.md"


def _is_instruction_include_file(target: Path) -> bool:
    return target.name.lower() in {
        "agents.md",
        "claude.md",
        "gemini.md",
        "instructions.md",
        "rules.md",
    }


def _adds_only_known_safe_include(before: str, after: str) -> bool:
    added_lines = _added_lines(before, after)
    if not added_lines:
        return False
    return all(_is_known_safe_include(line) for line in added_lines)


def _added_lines(before: str, after: str) -> list[str]:
    before_lines = before.splitlines()
    after_lines = after.splitlines()
    added: list[str] = []
    cursor = 0
    for line in before_lines:
        while cursor < len(after_lines) and after_lines[cursor] != line:
            stripped = after_lines[cursor].strip()
            if stripped:
                added.append(stripped)
            cursor += 1
        if cursor >= len(after_lines):
            return []
        cursor += 1
    for line in after_lines[cursor:]:
        stripped = line.strip()
        if stripped:
            added.append(stripped)
    return added


def _extract_backup_timestamp(backup_path: str, policy: ChronographSafetyPolicy) -> str | None:
    try:
        relative = Path(backup_path).resolve().relative_to(policy.backup_root.resolve())
    except ValueError:
        return None
    return relative.parts[0] if relative.parts else None


def _timestamp(now: datetime | None) -> str:
    value = now or datetime.now(tz=UTC)
    return value.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")


def _backup_path(
    policy: ChronographSafetyPolicy,
    target: Path,
    action_id: str,
    timestamp: str,
) -> Path:
    try:
        relative = target.relative_to(policy.repo_root)
    except ValueError:
        relative = Path(*[part for part in target.parts if part not in (target.anchor, "")])
    safe_relative = Path(*[_safe_path_part(part) for part in relative.parts])
    return policy.backup_root / timestamp / action_id / safe_relative


def _safe_path_part(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9._@-]+", "_", value) or "_"


def _rollback_command(backup_path: Path, target: Path, target_existed: bool) -> str:
    if not target_existed:
        return f"Remove-Item -LiteralPath '{_ps_escape(target)}' -Force"
    return f"Copy-Item -LiteralPath '{_ps_escape(backup_path)}' -Destination '{_ps_escape(target)}' -Force"


def _ps_escape(path: Path) -> str:
    return str(path).replace("'", "''")


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _append_audit(
    policy: ChronographSafetyPolicy,
    item: ApplyPlanItem,
    result: ApplyResult,
    *,
    now: datetime | None,
) -> None:
    policy.audit_path.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "timestamp": (now or datetime.now(tz=UTC)).astimezone(UTC).isoformat(),
        "action_id": item.action_id,
        "action_class": item.action_class.value,
        "target_file": item.target_path,
        "old_hash": item.old_hash,
        "new_hash": item.new_hash,
        "risk_level": item.risk_level.value,
        "backup_path": result.backup_path,
        "rollback_command": item.rollback_command,
        "target_existed": item.target_existed,
        "reason": item.reason,
        "narrative_ref": item.narrative_ref,
        "applied": result.applied,
        "skipped_reason": result.skipped_reason,
        "error": result.error,
    }
    with policy.audit_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, sort_keys=True) + "\n")


def _restore_after_failure(target: Path, backup: Path, existed: bool) -> None:
    if existed and backup.exists():
        shutil.copy2(backup, target)
    elif not existed and target.exists():
        target.unlink()


def _is_forbidden_path(lowered_parts: list[str], lowered_name: str) -> bool:
    forbidden_parts = {
        ".cache",
        "cache",
        "caches",
        "transcripts",
        "sessions",
        "session",
        "history",
        "histories",
        "logs",
        "log",
        "globalstorage",
    }
    forbidden_names = {
        ".env",
        "auth.json",
        "credentials.json",
        "token.json",
        "tokens.json",
        "secrets.json",
        "state.sqlite",
        "state.db",
    }
    forbidden_fragments = ("credential", "secret", "token", "auth")
    forbidden_suffixes = (
        ".db",
        ".db-shm",
        ".db-wal",
        ".duckdb",
        ".sqlite",
        ".sqlite-shm",
        ".sqlite-wal",
        ".sqlite3",
        ".sqlite3-shm",
        ".sqlite3-wal",
    )

    if any(part in forbidden_parts for part in lowered_parts):
        return True
    if lowered_name in forbidden_names:
        return True
    if any(lowered_name.endswith(suffix) for suffix in forbidden_suffixes):
        return True
    return any(fragment in lowered_name for fragment in forbidden_fragments)


def _is_allowed_live_file(target: Path) -> bool:
    name = target.name.lower()
    suffix = target.suffix.lower()
    parts = {part.lower() for part in target.parts}

    instruction_files = {
        "agents.md",
        "claude.md",
        "gemini.md",
        "rtk.md",
        "instructions.md",
        "rules.md",
    }
    if name in instruction_files:
        return True

    if name in {"settings.json", "config.json", "config.toml", "mcp.json", "lsp.json"}:
        return True

    if {"agents", "skills", "rules", "hooks", "memory", "memories"} & parts:
        return suffix in {".md", ".json", ".toml", ".yaml", ".yml", ".txt"}

    return False


def _is_under(path: Path, root: Path) -> bool:
    resolved = path.resolve()
    resolved_root = root.resolve()
    return resolved == resolved_root or resolved_root in resolved.parents
