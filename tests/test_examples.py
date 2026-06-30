"""Asserts the committed examples/ artifacts are valid and (re)generates
sample-dashboard.html deterministically when missing."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from guardian.dashboard import render_dashboard
from guardian.models import AntiPattern, NorthStar, Principle
from guardian.north_star import parse_north_star_markdown

_EXAMPLES_DIR = Path(__file__).parent.parent / "examples"
_NORTH_STAR_FILE = _EXAMPLES_DIR / "sample-northstar.md"
_DASHBOARD_FILE = _EXAMPLES_DIR / "sample-dashboard.html"


class _FakeStore:
    """In-memory stand-in for MemoryStore used only for dashboard generation."""

    def __init__(self) -> None:
        self._files: dict[str, str] = {}

    def read(self, path: str) -> str:
        if path not in self._files:
            raise FileNotFoundError(f"fake-store:{path}")
        return self._files[path]

    def read_json(self, path: str) -> Any:
        return json.loads(self.read(path))

    def exists(self, path: str) -> bool:
        return path in self._files

    def write(self, path: str, content: str, message: str = "") -> None:
        self._files[path] = content

    def write_json(self, path: str, obj: Any, message: str = "") -> None:
        self._files[path] = json.dumps(obj, indent=2, default=str)

    def list(self, prefix: str = "") -> list[str]:
        if prefix:
            return sorted(k for k in self._files if k.startswith(prefix))
        return sorted(self._files.keys())


_DT_BASE = datetime(2026, 2, 1, 10, 0, 0, tzinfo=UTC)


def _dt(days_offset: int, hour: int = 10) -> datetime:
    return _DT_BASE + timedelta(days=days_offset, hours=hour - 10)


def _seed_store() -> tuple[_FakeStore, NorthStar]:
    """Return a FakeStore pre-loaded with sagas, journal entries, and a North Star."""
    store = _FakeStore()

    # -- North Star --------------------------------------------------------
    north_star = NorthStar(
        version=1,
        project_name="LumenScout",
        identity_statement=(
            "LumenScout is an LLM-powered, local-first research assistant. "
            "It is not a cloud sync tool or a general-purpose search engine."
        ),
        principles=[
            Principle(
                id="p1",
                rank=1,
                text="All synthesis flows through the LLM reasoning layer.",
                tags=["[OWNER]", "{LOCKED}", "«CORE»"],
            ),
            Principle(
                id="p2",
                rank=2,
                text="Local-first always — no user content transmitted without explicit opt-in.",
                tags=["[OWNER]", "{LOCKED}", "«CORE»"],
            ),
            Principle(
                id="p3",
                rank=3,
                text="Use LlamaIndex for all document ingestion and retrieval.",
                tags=["[OWNER]", "{LOCKED}", "«CORE»"],
            ),
            Principle(
                id="p4",
                rank=4,
                text="Every user-facing response must cite at least one source document.",
                tags=["[INFERRED]", "{DRAFT}", "«SECONDARY»"],
            ),
            Principle(
                id="p5",
                rank=5,
                text="FastAPI is a convenience layer only — logic must work without the server.",
                tags=["[INFERRED]", "{DRAFT}", "«OPTIONAL»"],
            ),
        ],
        approved_architecture=(
            "Python 3.11+, Pydantic v2, LlamaIndex, Anthropic SDK, SQLite/aiosqlite, "
            "FastAPI (optional), Jinja2. Local FAISS index; no hosted vector databases."
        ),
        anti_patterns=[
            AntiPattern(
                id="ap1",
                description="Standalone scripts that bypass the LLM reasoning layer.",
                example="scripts/extract_keywords.py running spaCy NER directly.",
                detect="scripts/*.py",
            ),
            AntiPattern(
                id="ap2",
                description="Outbound calls to external embedding APIs without user opt-in.",
                detect="openai|cohere|voyage",
            ),
            AntiPattern(
                id="ap3",
                description="Introducing a second retrieval library alongside LlamaIndex.",
                detect="from langchain|import haystack",
            ),
        ],
        created_at=_dt(0),
    )

    # -- Sagas ---------------------------------------------------------------
    sagas = [
        {
            "id": "initial-ingestion-pipeline",
            "name": "Initial Ingestion Pipeline",
            "start_date": _dt(0).isoformat(),
            "end_date": _dt(14).isoformat(),
            "status": "completed",
            "pr_numbers": [1, 2, 3],
            "description": "Built the core document ingestion pipeline using LlamaIndex.",
        },
        {
            "id": "caching-layer-exploration",
            "name": "Caching Layer Exploration",
            "start_date": _dt(15).isoformat(),
            "end_date": None,
            "status": "active",
            "pr_numbers": [17, 19],
            "description": "Exploring caching strategies for embeddings and LLM responses.",
        },
        {
            "id": "citation-engine",
            "name": "Citation Engine",
            "start_date": _dt(20).isoformat(),
            "end_date": None,
            "status": "active",
            "pr_numbers": [22],
            "description": "Implementing source citation linking for all user-facing responses.",
        },
    ]
    store.write_json("memory/sagas/_index.json", {"sagas": sagas})

    # -- Journal entries -----------------------------------------------------
    journal_entries = [
        # PR 1 — aligned
        (
            "memory/journal/2026-02-01-pr-1.md",
            "\n".join(
                [
                    "---",
                    "pr_number: 1",
                    "saga_id: initial-ingestion-pipeline",
                    "verdict: aligned",
                    f"timestamp: {_dt(0).strftime('%Y-%m-%d %H:%M:%S UTC')}",
                    "---",
                    "",
                    "# PR #1 — ALIGNED",
                    "",
                    "PR #1 introduced the core LlamaIndex ingestion pipeline, establishing "
                    "the document loading, chunking, and FAISS indexing flow. All processing "
                    "runs locally with no external service calls.",
                    "",
                    "**Alignment Summary:** Fully aligned with Principles 1, 2, and 3.",
                    "",
                    "## Principle Evaluations",
                    "",
                    "| Principle | Verdict | Reasoning |",
                    "|-----------|---------|-----------|",
                    "| `p1` | aligned | All synthesis flows through the LLM layer. |",
                    "| `p2` | aligned | No outbound calls; FAISS index is local. |",
                    "| `p3` | aligned | Uses LlamaIndex VectorStoreIndex throughout. |",
                    "",
                    "**Saga:** Initial Ingestion Pipeline (`initial-ingestion-pipeline`)",
                    "",
                ]
            ),
        ),
        # PR 17 — ambiguous
        (
            "memory/journal/2026-02-16-pr-17.md",
            "\n".join(
                [
                    "---",
                    "pr_number: 17",
                    "saga_id: caching-layer-exploration",
                    "verdict: ambiguous",
                    f"timestamp: {_dt(15).strftime('%Y-%m-%d %H:%M:%S UTC')}",
                    "---",
                    "",
                    "# PR #17 — AMBIGUOUS",
                    "",
                    "PR #17 introduced a CacheBackend abstraction with InMemoryCache and "
                    "RedisCache implementations. The Redis path accepts an arbitrary URL, "
                    "raising the question of whether hosted Redis would be permitted.",
                    "",
                    "**Alignment Summary:** Ambiguous — local-first guarantee unclear for RedisCache.",
                    "",
                    "## Principle Evaluations",
                    "",
                    "| Principle | Verdict | Reasoning |",
                    "|-----------|---------|-----------|",
                    "| `p2` | ambiguous | RedisCache accepts any URL, including hosted services. |",
                    "",
                    "**Saga:** Caching Layer Exploration (`caching-layer-exploration`)",
                    "",
                ]
            ),
        ),
        # PR 22 — drift
        (
            "memory/journal/2026-02-21-pr-22.md",
            "\n".join(
                [
                    "---",
                    "pr_number: 22",
                    "saga_id: citation-engine",
                    "verdict: drift",
                    f"timestamp: {_dt(20).strftime('%Y-%m-%d %H:%M:%S UTC')}",
                    "---",
                    "",
                    "# PR #22 — DRIFT",
                    "",
                    "PR #22 added a summary endpoint that returns LLM-synthesised text without "
                    "any source citations. This directly conflicts with Principle 4.",
                    "",
                    "**Alignment Summary:** Drift detected — Principle 4 violated.",
                    "",
                    "## Principle Evaluations",
                    "",
                    "| Principle | Verdict | Reasoning |",
                    "|-----------|---------|-----------|",
                    "| `p4` | drift | Summary response contains no citation links. |",
                    "",
                    "**Saga:** Citation Engine (`citation-engine`)",
                    "",
                ]
            ),
        ),
    ]
    for path, content in journal_entries:
        store.write(path, content)

    # -- Drift ledger --------------------------------------------------------
    drift_events = [
        {
            "id": "de-001",
            "pr_number": 22,
            "principle_id": "p4",
            "severity": "medium",
            "details": "Summary endpoint returns synthesised text with no source citations.",
            "timestamp": _dt(20).isoformat(),
        }
    ]
    store.write_json("memory/drift-ledger.json", {"events": drift_events})

    return store, north_star


@pytest.fixture(scope="module")
def north_star() -> NorthStar:
    return parse_north_star_markdown(_NORTH_STAR_FILE.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def dashboard_html() -> str:
    if not _DASHBOARD_FILE.exists():
        store, c = _seed_store()
        _EXAMPLES_DIR.mkdir(parents=True, exist_ok=True)
        _DASHBOARD_FILE.write_text(render_dashboard(store, c), encoding="utf-8")
    return _DASHBOARD_FILE.read_text(encoding="utf-8")


def test_north_star_file_exists() -> None:
    assert _NORTH_STAR_FILE.exists()


def test_north_star_has_five_principles(north_star: NorthStar) -> None:
    assert len(north_star.principles) == 5


def test_north_star_has_three_anti_patterns(north_star: NorthStar) -> None:
    assert len(north_star.anti_patterns) == 3


def test_north_star_has_identity_statement(north_star: NorthStar) -> None:
    assert north_star.identity_statement.strip()


def test_dashboard_contains_mermaid_cdn(dashboard_html: str) -> None:
    assert "cdn.jsdelivr.net" in dashboard_html


@pytest.mark.parametrize(
    "heading",
    ["Saga Timeline", "Branch Topology", "Strategic Quadrant", "Principle Map"],
)
def test_dashboard_has_section(dashboard_html: str, heading: str) -> None:
    assert heading in dashboard_html, f"missing section {heading!r}"
