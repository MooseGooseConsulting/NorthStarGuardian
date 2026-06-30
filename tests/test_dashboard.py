"""Tests for guardian.dashboard.

Verifies that each generate_* function returns valid Mermaid source starting
with the correct directive, and that render_dashboard produces parseable HTML
containing the expected sections.
"""

from __future__ import annotations

from datetime import UTC, datetime
from html.parser import HTMLParser

from guardian.dashboard import (
    generate_gantt,
    generate_gitgraph,
    generate_mindmap,
    generate_quadrant,
    render_dashboard,
)
from guardian.models import (
    AntiPattern,
    DriftEvent,
    DriftSeverity,
    JournalEntry,
    NorthStar,
    Principle,
    Saga,
    SagaStatus,
    Verdict,
)
from tests.conftest import FakeStore

# ---------------------------------------------------------------------------
# HTML validation helper
# ---------------------------------------------------------------------------


class _HTMLChecker(HTMLParser):
    """Minimal HTMLParser subclass that records seen tags and text content."""

    def __init__(self) -> None:
        super().__init__()
        self.tags: list[str] = []
        self._text_chunks: list[str] = []
        self._errors: list[str] = []

    def handle_starttag(self, tag: str, attrs: list) -> None:
        self.tags.append(tag)

    def handle_data(self, data: str) -> None:
        self._text_chunks.append(data)

    def full_text(self) -> str:
        return " ".join(self._text_chunks)

    def error(self, message: str) -> None:  # type: ignore[override]
        self._errors.append(message)


def _parse_html(html: str) -> _HTMLChecker:
    checker = _HTMLChecker()
    checker.feed(html)
    return checker


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_north_star(project_name: str = "TestProject") -> NorthStar:
    return NorthStar(
        project_name=project_name,
        identity_statement="An LLM-powered pipeline. Not a script collection.",
        principles=[
            Principle(
                id="p1",
                rank=1,
                text="All analysis must flow through the LLM layer.",
            ),
            Principle(
                id="p2",
                rank=2,
                text="Prefer coldvox over raw whisper bindings.",
            ),
        ],
        approved_architecture="Gin, GORM, LangChain",
        anti_patterns=[
            AntiPattern(
                id="ap1",
                description="Raw CSV scripts that bypass the LLM layer.",
            )
        ],
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


def _make_saga(
    saga_id: str = "auth-overhaul",
    name: str = "The Authentication Overhaul",
    *,
    status: SagaStatus = SagaStatus.ACTIVE,
    pr_numbers: list[int] | None = None,
) -> Saga:
    return Saga(
        id=saga_id,
        name=name,
        start_date=datetime(2026, 1, 1, tzinfo=UTC),
        end_date=datetime(2026, 3, 31, tzinfo=UTC),
        status=status,
        pr_numbers=pr_numbers or [1, 2],
        description="Overhauling the auth subsystem.",
    )


def _make_journal_entries() -> list[JournalEntry]:
    return [
        JournalEntry(
            pr_number=1,
            timestamp=datetime(2026, 1, 10, tzinfo=UTC),
            saga_id="auth-overhaul",
            verdict=Verdict.ALIGNED,
            body_markdown="| `p1` | aligned | Used LLM layer. |",
        ),
        JournalEntry(
            pr_number=2,
            timestamp=datetime(2026, 2, 5, tzinfo=UTC),
            saga_id="auth-overhaul",
            verdict=Verdict.DRIFT,
            body_markdown="| `p2` | drift | Used whisper directly. |",
        ),
    ]


def _make_drift_events() -> list[DriftEvent]:
    return [
        DriftEvent(
            id="de-1",
            pr_number=2,
            principle_id="p2",
            severity=DriftSeverity.HIGH,
            details="Whisper import found.",
            timestamp=datetime(2026, 2, 5, tzinfo=UTC),
        )
    ]


# ---------------------------------------------------------------------------
# generate_gantt
# ---------------------------------------------------------------------------


class TestGenerateGantt:
    def test_starts_with_gantt_directive(self) -> None:
        sagas = [_make_saga()]
        result = generate_gantt(sagas)
        assert result.strip().startswith("gantt")

    def test_contains_saga_name(self) -> None:
        sagas = [_make_saga(name="The Big Refactor")]
        result = generate_gantt(sagas)
        assert "The Big Refactor" in result

    def test_contains_pr_bars(self) -> None:
        sagas = [_make_saga(pr_numbers=[10, 20])]
        result = generate_gantt(sagas)
        assert "PR #10" in result
        assert "PR #20" in result

    def test_empty_sagas_still_valid_mermaid(self) -> None:
        result = generate_gantt([])
        assert result.strip().startswith("gantt")
        # Should contain a placeholder so Mermaid doesn't crash
        assert "No data" in result or "No Sagas" in result

    def test_completed_saga_has_done_modifier(self) -> None:
        saga = _make_saga(status=SagaStatus.COMPLETED)
        result = generate_gantt([saga])
        assert "done" in result

    def test_multiple_sagas_each_have_section(self) -> None:
        sagas = [
            _make_saga("s1", "Saga One"),
            _make_saga("s2", "Saga Two"),
        ]
        result = generate_gantt(sagas)
        assert "Saga One" in result
        assert "Saga Two" in result


# ---------------------------------------------------------------------------
# generate_gitgraph
# ---------------------------------------------------------------------------


class TestGenerateGitGraph:
    def test_starts_with_gitgraph_directive(self) -> None:
        result = generate_gitgraph([], [])
        assert result.strip().startswith("gitGraph")

    def test_pr_commits_appear(self) -> None:
        journal = _make_journal_entries()
        result = generate_gitgraph([], journal)
        assert "PR #1" in result
        assert "PR #2" in result

    def test_drift_branch_created_for_drift_prs(self) -> None:
        journal = _make_journal_entries()
        drift = _make_drift_events()
        result = generate_gitgraph(drift, journal)
        assert "branch drift" in result

    def test_drift_event_annotated_with_principle(self) -> None:
        journal = _make_journal_entries()
        drift = _make_drift_events()
        result = generate_gitgraph(drift, journal)
        # Drift event for PR #2 involves principle p2
        assert "p2" in result

    def test_empty_inputs_still_valid(self) -> None:
        result = generate_gitgraph([], [])
        assert "gitGraph" in result


# ---------------------------------------------------------------------------
# generate_quadrant
# ---------------------------------------------------------------------------


class TestGenerateQuadrant:
    def test_starts_with_quadrant_directive(self) -> None:
        result = generate_quadrant(journal=[])
        assert result.strip().startswith("quadrantChart")

    def test_pr_points_appear(self) -> None:
        journal = _make_journal_entries()
        result = generate_quadrant(journal=journal)
        assert "PR#1" in result
        assert "PR#2" in result

    def test_coordinates_in_range(self) -> None:
        journal = _make_journal_entries()
        result = generate_quadrant(journal=journal)
        # Each data point line looks like "    PR#N: [X, Y]"
        import re

        matches = re.findall(r"\[([0-9.]+),\s*([0-9.]+)\]", result)
        for x_str, y_str in matches:
            x, y = float(x_str), float(y_str)
            assert 0.0 <= x <= 1.0, f"x={x} out of range"
            assert 0.0 <= y <= 1.0, f"y={y} out of range"

    def test_empty_journal_returns_header_only(self) -> None:
        result = generate_quadrant(journal=[])
        assert "quadrantChart" in result

    def test_accepts_reports_keyword_arg(self) -> None:
        # reports parameter is accepted but not required
        journal = _make_journal_entries()
        result = generate_quadrant(journal=journal, reports=None)
        assert "quadrantChart" in result


# ---------------------------------------------------------------------------
# generate_mindmap
# ---------------------------------------------------------------------------


class TestGenerateMindmap:
    def test_starts_with_mindmap_directive(self) -> None:
        north_star = _make_north_star()
        result = generate_mindmap(north_star, [])
        assert result.strip().startswith("mindmap")

    def test_project_name_is_root(self) -> None:
        north_star = _make_north_star("MyProject")
        result = generate_mindmap(north_star, [])
        assert "MyProject" in result

    def test_principles_appear_as_branches(self) -> None:
        north_star = _make_north_star()
        result = generate_mindmap(north_star, [])
        for p in north_star.principles:
            assert p.text[:20] in result

    def test_prs_attached_to_principles(self) -> None:
        north_star = _make_north_star()
        journal = _make_journal_entries()
        result = generate_mindmap(north_star, journal)
        # PR #1 mentions p1 in its body; it should appear somewhere in the chart
        assert "PR #1" in result

    def test_unattached_prs_go_to_unassigned(self) -> None:
        north_star = _make_north_star()
        journal = [
            JournalEntry(
                pr_number=99,
                timestamp=datetime(2026, 1, 1, tzinfo=UTC),
                saga_id=None,
                verdict=Verdict.AMBIGUOUS,
                body_markdown="No principle table here.",
            )
        ]
        result = generate_mindmap(north_star, journal)
        assert "Unassigned" in result
        assert "PR #99" in result

    def test_empty_journal_valid(self) -> None:
        north_star = _make_north_star()
        result = generate_mindmap(north_star, [])
        assert "mindmap" in result


# ---------------------------------------------------------------------------
# render_dashboard
# ---------------------------------------------------------------------------


class TestRenderDashboard:
    def _make_store_with_data(self) -> FakeStore:
        store = FakeStore()

        # Seed saga index
        sagas = [_make_saga()]
        store.write_json(
            "memory/sagas/_index.json",
            {
                "sagas": [
                    {
                        "id": s.id,
                        "name": s.name,
                        "start_date": s.start_date.isoformat(),
                        "end_date": s.end_date.isoformat() if s.end_date else None,
                        "status": s.status.value,
                        "pr_numbers": s.pr_numbers,
                        "description": s.description,
                    }
                    for s in sagas
                ]
            },
        )

        # Seed journal entries
        for entry in _make_journal_entries():
            date_str = entry.timestamp.strftime("%Y-%m-%d")
            path = f"memory/journal/{date_str}-pr-{entry.pr_number}.md"
            ts_str = entry.timestamp.strftime("%Y-%m-%d %H:%M:%S UTC")
            store.write(
                path,
                "\n".join(
                    [
                        "---",
                        f"pr_number: {entry.pr_number}",
                        f"saga_id: {entry.saga_id or 'unassigned'}",
                        f"verdict: {entry.verdict.value}",
                        f"timestamp: {ts_str}",
                        "---",
                        "",
                        entry.body_markdown,
                    ]
                ),
            )

        # Seed drift ledger
        drift = _make_drift_events()
        store.write_json(
            "memory/drift-ledger.json",
            {
                "events": [
                    {
                        "id": de.id,
                        "pr_number": de.pr_number,
                        "principle_id": de.principle_id,
                        "severity": de.severity.value,
                        "details": de.details,
                        "timestamp": de.timestamp.isoformat(),
                    }
                    for de in drift
                ]
            },
        )

        return store

    def test_returns_non_empty_string(self) -> None:
        store = self._make_store_with_data()
        north_star = _make_north_star()
        result = render_dashboard(store, north_star)
        assert isinstance(result, str)
        assert len(result) > 100

    def test_valid_html_no_parser_errors(self) -> None:
        store = self._make_store_with_data()
        north_star = _make_north_star()
        html = render_dashboard(store, north_star)
        checker = _parse_html(html)
        assert checker._errors == []

    def test_contains_html_and_body_tags(self) -> None:
        store = self._make_store_with_data()
        north_star = _make_north_star()
        html = render_dashboard(store, north_star)
        checker = _parse_html(html)
        assert "html" in checker.tags
        assert "body" in checker.tags

    def test_contains_project_name(self) -> None:
        store = self._make_store_with_data()
        north_star = _make_north_star("AwesomeProject")
        html = render_dashboard(store, north_star)
        assert "AwesomeProject" in html

    def test_contains_mermaid_cdn_script(self) -> None:
        store = self._make_store_with_data()
        north_star = _make_north_star()
        html = render_dashboard(store, north_star)
        assert "cdn.jsdelivr.net/npm/mermaid@10" in html

    def test_contains_four_chart_sections(self) -> None:
        store = self._make_store_with_data()
        north_star = _make_north_star()
        html = render_dashboard(store, north_star)
        # Section headings from the template
        assert "Saga Timeline" in html
        assert "Branch Topology" in html
        assert "Strategic Quadrant" in html
        assert "Principle Map" in html

    def test_contains_principles_in_sidebar(self) -> None:
        store = self._make_store_with_data()
        north_star = _make_north_star()
        html = render_dashboard(store, north_star)
        for p in north_star.principles:
            assert p.text[:20] in html

    def test_contains_generation_timestamp(self) -> None:
        store = self._make_store_with_data()
        north_star = _make_north_star()
        html = render_dashboard(store, north_star)
        assert "NorthStarGuardian" in html

    def test_written_to_store(self) -> None:
        store = self._make_store_with_data()
        north_star = _make_north_star()
        render_dashboard(store, north_star)
        assert store.exists("memory/dashboard.html")

    def test_mermaid_directives_embedded(self) -> None:
        store = self._make_store_with_data()
        north_star = _make_north_star()
        html = render_dashboard(store, north_star)
        assert "gantt" in html
        assert "gitGraph" in html
        assert "quadrantChart" in html
        assert "mindmap" in html

    def test_light_dark_css_present(self) -> None:
        store = self._make_store_with_data()
        north_star = _make_north_star()
        html = render_dashboard(store, north_star)
        assert "prefers-color-scheme" in html

    def test_no_external_assets_except_mermaid(self) -> None:
        store = self._make_store_with_data()
        north_star = _make_north_star()
        html = render_dashboard(store, north_star)
        import re

        # Find all src= and href= attributes
        src_refs = re.findall(r'(?:src|href)=["\']([^"\']+)["\']', html)
        for ref in src_refs:
            if ref.startswith("http"):
                assert "jsdelivr.net" in ref, f"Unexpected external asset: {ref!r}"
