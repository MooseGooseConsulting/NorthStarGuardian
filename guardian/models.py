"""Shared data models for NorthStarGuardian.

This module is the single source of truth for cross-module types. Every other
module imports from here. Modules MUST NOT define their own variants of these
types — extend here instead.

Provenance / lifecycle / priority tagging follows the multi-axis proposal in
docs/ADDENDUM.md §A3:

    Provenance: [OWNER] [INFERRED] [OPEN]              -- where it came from
    Lifecycle:  {LOCKED} {DRAFT} {CONTESTED}           -- how settled it is
    Priority:   «CORE» «SECONDARY» «OPTIONAL»          -- how load-bearing

These are stored as strings on the Principle model; the closed vocabularies
are documented here but not enforced at the type level so amendments don't
require code changes.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum, StrEnum
from typing import Literal

from pydantic import BaseModel, Field, model_validator

# ---------------------------------------------------------------------------
# North Star
# ---------------------------------------------------------------------------


class Principle(BaseModel):
    """A single ranked tenet in the North Star."""

    id: str
    rank: int
    text: str
    rationale: str | None = None
    tags: list[str] = Field(default_factory=list)


class AntiPattern(BaseModel):
    """An explicit example of what drift looks like in this project."""

    id: str
    description: str
    example: str | None = None
    detect: str | None = None  # optional grep/AST hint


class NorthStar(BaseModel):
    """The project's identity document, version-pinned for amendment tracking."""

    version: int = 1
    project_name: str
    identity_statement: str
    principles: list[Principle]
    approved_architecture: str
    anti_patterns: list[AntiPattern] = Field(default_factory=list)
    created_at: datetime
    amended_at: datetime | None = None


class Amendment(BaseModel):
    """Append-only record of North Star changes."""

    timestamp: datetime
    actor: str
    target: Literal["identity", "principle", "anti_pattern", "architecture"]
    target_id: str | None
    before: str | None
    after: str
    rationale: str


# ---------------------------------------------------------------------------
# Diff / Interview
# ---------------------------------------------------------------------------


class Verdict(StrEnum):
    ALIGNED = "aligned"
    AMBIGUOUS = "ambiguous"
    DRIFT = "drift"


class FileChange(BaseModel):
    path: str
    status: Literal["added", "modified", "removed", "renamed"]
    additions: int
    deletions: int
    new_imports: list[str] = Field(default_factory=list)
    removed_imports: list[str] = Field(default_factory=list)
    patch_available: bool = True


class DiffAnalysis(BaseModel):
    """Structural information extracted from a PR diff. Pre-LLM."""

    pr_number: int
    pr_title: str
    pr_body: str | None
    author: str
    base_sha: str
    head_sha: str
    files: list[FileChange]
    new_packages: list[str] = Field(default_factory=list)
    summary_stats: dict[str, int] = Field(default_factory=dict)


class PrincipleEvaluation(BaseModel):
    """Per-principle verdict produced by `evaluate_alignment`."""

    principle_id: str
    relevant: bool
    verdict: Verdict | None = None
    reasoning: str | None = None
    citations: list[str] = Field(default_factory=list)  # file:line refs


class AntiPatternMatch(BaseModel):
    pattern_id: str
    location: str  # file:line
    explanation: str


class IntentSummary(BaseModel):
    """Inferred goal of the PR. Output of `assess_intent`."""

    one_line: str
    paragraph: str
    declared_variances: list[VarianceTag] = Field(default_factory=list)


class VarianceTag(BaseModel):
    """Parsed `[VARIANCE: ...]` declaration from PR body."""

    principle_id: str
    justification: str
    expires_in_days: int
    raw: str


class InterviewReport(BaseModel):
    """Top-level Guardian output for a single PR."""

    pr_number: int
    overall_verdict: Verdict
    alignment_summary: str
    principle_evaluations: list[PrincipleEvaluation]
    anti_pattern_matches: list[AntiPatternMatch] = Field(default_factory=list)
    saga_id: str | None
    suggestions: list[str] = Field(default_factory=list)
    chronicle_paragraph: str
    intent: IntentSummary
    created_at: datetime


# ---------------------------------------------------------------------------
# Chronicle
# ---------------------------------------------------------------------------


class SagaStatus(StrEnum):
    ACTIVE = "active"
    COMPLETED = "completed"
    ABANDONED = "abandoned"


class Saga(BaseModel):
    id: str  # slug, e.g. "authentication-overhaul"
    name: str
    start_date: datetime
    end_date: datetime | None = None
    status: SagaStatus = SagaStatus.ACTIVE
    pr_numbers: list[int] = Field(default_factory=list)
    description: str = ""


class JournalEntry(BaseModel):
    pr_number: int
    timestamp: datetime
    saga_id: str | None
    verdict: Verdict
    body_markdown: str


# ---------------------------------------------------------------------------
# Governance (drift, variance, debt)
# ---------------------------------------------------------------------------


class DriftSeverity(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class DriftEvent(BaseModel):
    id: str
    pr_number: int
    principle_id: str
    severity: DriftSeverity
    details: str
    timestamp: datetime


class DebtLevel(int, Enum):
    NEUTRAL = 0
    REMINDER_75 = 1
    REMINDER_EXPIRED = 2
    BLOCKING = 3


class DebtTimer(BaseModel):
    id: str
    pr_number: int
    principle_id: str
    justification: str
    created_at: datetime
    expires_at: datetime
    resolved_at: datetime | None = None
    level: DebtLevel = DebtLevel.NEUTRAL
    affected_paths: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


class NorthStarConfig(BaseModel):
    """North Star source configuration."""

    source: Literal["repo", "linear"] = "repo"
    repo_path: str = "docs/northstar.md"
    active_copy_path: str = ".github/guardian/northstar.md"


class LinearConfig(BaseModel):
    """Linear integration configuration."""

    document_id: str | None = None
    workspace_url: str | None = None
    team_id: str | None = None
    project_id: str | None = None
    initiative_id: str | None = None


class GuardianConfig(BaseModel):
    """Runtime configuration stored at `.github/guardian/guardian-config.json`."""

    north_star: NorthStarConfig = Field(default_factory=NorthStarConfig)
    linear: LinearConfig = Field(default_factory=LinearConfig)
    openai_model_analysis: str = "gpt-5"
    openai_model_initialization: str = "gpt-5"
    enable_blocking_escalation: bool = False
    """Whether expired debt timers escalate to blocking future PRs.

    Default off to honor the North Star anti-goal "the Guardian must never
    become an authority." Operators can opt in per repo by flipping this in
    `.github/guardian/guardian-config.json`. The escalation machinery is still recorded
    even when this is False — only the merge-block side effect is gated.
    """

    variance_default_days: int = 7
    pages_url: str | None = None

    @model_validator(mode="before")
    @classmethod
    def migrate_legacy_flat_source_settings(cls, value: object) -> object:
        """Normalize legacy flat North Star and Linear fields to nested settings."""
        if not isinstance(value, dict):
            return value

        data = dict(value)
        north_star = dict(data.get("north_star") or {})
        linear = dict(data.get("linear") or {})
        aliases = {
            "source": (north_star, "source"),
            "north_star_source": (north_star, "source"),
            "north_star_repo_path": (north_star, "repo_path"),
            "repo_north_star_path": (north_star, "repo_path"),
            "repo_path": (north_star, "repo_path"),
            "north_star_active_copy_path": (north_star, "active_copy_path"),
            "active_north_star_path": (north_star, "active_copy_path"),
            "active_copy_path": (north_star, "active_copy_path"),
            "linear_document_id": (linear, "document_id"),
            "linear_workspace_url": (linear, "workspace_url"),
            "linear_team_id": (linear, "team_id"),
            "linear_project_id": (linear, "project_id"),
            "linear_initiative_id": (linear, "initiative_id"),
            "document_id": (linear, "document_id"),
            "workspace_url": (linear, "workspace_url"),
            "team_id": (linear, "team_id"),
            "project_id": (linear, "project_id"),
            "initiative_id": (linear, "initiative_id"),
        }
        for old_name, (section, field_name) in aliases.items():
            if old_name in data:
                section.setdefault(field_name, data.pop(old_name))

        if north_star:
            data["north_star"] = north_star
        if linear:
            data["linear"] = linear
        return data
