"""Tests for guardian.chronicle.

Uses a FakeStore that avoids any git subprocess calls.
The OpenAI client is mocked for assign_saga tests.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from unittest.mock import MagicMock

from guardian.chronicle import (
    assign_saga,
    read_chronicle,
    update_saga,
    write_journal_entry,
)
from guardian.models import (
    IntentSummary,
    InterviewReport,
    JournalEntry,
    PrincipleEvaluation,
    Saga,
    SagaStatus,
    Verdict,
)

# ---------------------------------------------------------------------------
# FakeStore
# ---------------------------------------------------------------------------


class FakeStore:
    """In-memory stand-in for MemoryStore — no git, no filesystem."""

    def __init__(self) -> None:
        self._files: dict[str, str] = {}

    # MemoryStore interface
    def read(self, path: str) -> str:
        if path not in self._files:
            raise FileNotFoundError(f"FakeStore: {path!r} not found")
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


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_report(
    pr_number: int = 1,
    verdict: Verdict = Verdict.ALIGNED,
    *,
    saga_id: str | None = None,
) -> InterviewReport:
    ts = datetime(2026, 2, 5, 12, 0, 0, tzinfo=UTC)
    return InterviewReport(
        pr_number=pr_number,
        overall_verdict=verdict,
        alignment_summary="This aligns with the project identity.",
        principle_evaluations=[
            PrincipleEvaluation(
                principle_id="p1",
                relevant=True,
                verdict=verdict,
                reasoning="Core LLM layer used correctly.",
            )
        ],
        anti_pattern_matches=[],
        saga_id=saga_id,
        suggestions=["Consider adding a docstring here."],
        chronicle_paragraph=(
            "On Feb 5 the team introduced a WebSocket layer for real-time updates."
        ),
        intent=IntentSummary(
            one_line="Add WebSocket support",
            paragraph="The PR adds a WebSocket layer to enable real-time updates.",
        ),
        created_at=ts,
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
        status=status,
        pr_numbers=pr_numbers or [],
        description="Overhauling the auth subsystem.",
    )


# ---------------------------------------------------------------------------
# write_journal_entry
# ---------------------------------------------------------------------------


class TestWriteJournalEntry:
    def test_returns_journal_entry(self) -> None:
        store = FakeStore()
        report = _make_report(pr_number=42)
        saga = _make_saga()

        entry = write_journal_entry(store, report, saga)

        assert isinstance(entry, JournalEntry)
        assert entry.pr_number == 42
        assert entry.verdict == Verdict.ALIGNED
        assert entry.saga_id == "auth-overhaul"

    def test_file_written_to_correct_path(self) -> None:
        store = FakeStore()
        report = _make_report(pr_number=42)

        write_journal_entry(store, report, None)

        expected_path = "memory/journal/2026-02-05-pr-42.md"
        assert store.exists(expected_path), f"Expected {expected_path!r} in store"

    def test_frontmatter_round_trips(self) -> None:
        store = FakeStore()
        report = _make_report(pr_number=7, verdict=Verdict.DRIFT)
        saga = _make_saga(saga_id="debt-crisis", name="The Debt Crisis")

        write_journal_entry(store, report, saga)

        raw = store.read("memory/journal/2026-02-05-pr-7.md")
        # Split frontmatter
        parts = raw.split("---", 2)
        assert len(parts) == 3, "Expected YAML frontmatter delimiters"
        fm_text = parts[1]

        assert "pr_number: 7" in fm_text
        assert "saga_id: debt-crisis" in fm_text
        assert "verdict: drift" in fm_text
        assert "timestamp:" in fm_text

    def test_body_contains_chronicle_paragraph(self) -> None:
        store = FakeStore()
        report = _make_report(pr_number=1)
        write_journal_entry(store, report, None)

        raw = store.read("memory/journal/2026-02-05-pr-1.md")
        assert "WebSocket layer" in raw

    def test_body_contains_principle_table(self) -> None:
        store = FakeStore()
        report = _make_report(pr_number=1)
        write_journal_entry(store, report, None)

        raw = store.read("memory/journal/2026-02-05-pr-1.md")
        assert "| `p1`" in raw

    def test_no_saga_uses_unassigned(self) -> None:
        store = FakeStore()
        report = _make_report(pr_number=3)
        entry = write_journal_entry(store, report, None)

        assert entry.saga_id is None
        raw = store.read("memory/journal/2026-02-05-pr-3.md")
        assert "saga_id: unassigned" in raw

    def test_suggestions_appear_in_body(self) -> None:
        store = FakeStore()
        report = _make_report(pr_number=5)
        write_journal_entry(store, report, None)

        raw = store.read("memory/journal/2026-02-05-pr-5.md")
        assert "Consider adding a docstring here." in raw


# ---------------------------------------------------------------------------
# assign_saga
# ---------------------------------------------------------------------------


class TestAssignSaga:
    def _make_client(self, response_text: str) -> MagicMock:
        """Return a mock OpenAI client whose chat.completions.create returns *response_text*."""
        content_block = MagicMock()
        content_block.content = response_text
        choice = MagicMock()
        choice.message = content_block
        message = MagicMock()
        message.choices = [choice]
        client = MagicMock()
        client.chat.completions.create.return_value = message
        return client

    def test_match_existing_saga(self) -> None:
        store = FakeStore()
        saga = _make_saga(saga_id="auth-overhaul")
        intent = IntentSummary(
            one_line="Improve auth flow",
            paragraph="Refactors the login screen.",
        )
        client = self._make_client("MATCH: auth-overhaul")

        result = assign_saga(store, intent, [saga], client=client, model="claude-test")

        assert result.id == "auth-overhaul"
        assert result.name == "The Authentication Overhaul"

    def test_create_new_saga(self) -> None:
        store = FakeStore()
        intent = IntentSummary(
            one_line="Add real-time dashboard",
            paragraph="Introduces a WebSocket dashboard.",
        )
        client = self._make_client("CREATE: The Dashboard Initiative")

        result = assign_saga(store, intent, [], client=client, model="claude-test")

        assert result.id == "the-dashboard-initiative"
        assert result.name == "The Dashboard Initiative"
        assert result.status == SagaStatus.ACTIVE

    def test_new_saga_persisted_to_store(self) -> None:
        store = FakeStore()
        intent = IntentSummary(
            one_line="Add metrics",
            paragraph="Adds a metrics endpoint.",
        )
        client = self._make_client("CREATE: The Metrics Saga")

        result = assign_saga(store, intent, [], client=client, model="claude-test")

        saga_path = f"memory/sagas/{result.id}.md"
        assert store.exists(saga_path), f"Saga file not written: {saga_path}"
        assert store.exists("memory/sagas/_index.json"), "Index not updated"

    def test_index_updated_for_new_saga(self) -> None:
        store = FakeStore()
        intent = IntentSummary(
            one_line="Refactor DB layer",
            paragraph="Replaces raw SQL with ORM.",
        )
        client = self._make_client("CREATE: The ORM Migration")

        result = assign_saga(store, intent, [], client=client, model="claude-test")

        index = store.read_json("memory/sagas/_index.json")
        ids = [entry["id"] for entry in index["sagas"]]
        assert result.id in ids

    def test_slug_uniqueness(self) -> None:
        store = FakeStore()
        existing = _make_saga(saga_id="the-metrics-saga")
        intent = IntentSummary(one_line="More metrics", paragraph="More metrics.")
        # LLM wants to create "The Metrics Saga" again
        client = self._make_client("CREATE: The Metrics Saga")

        result = assign_saga(store, intent, [existing], client=client, model="claude-test")

        assert result.id != "the-metrics-saga"
        assert result.id.startswith("the-metrics-saga")

    def test_invalid_match_falls_back_to_create(self) -> None:
        store = FakeStore()
        intent = IntentSummary(one_line="Something new", paragraph="New stuff.")
        # LLM returns a MATCH for a saga that doesn't exist
        client = self._make_client("MATCH: nonexistent-saga-id")

        result = assign_saga(store, intent, [], client=client, model="claude-test")

        # Should fall back to creating a new saga (the fallback name)
        assert result.status == SagaStatus.ACTIVE


# ---------------------------------------------------------------------------
# update_saga
# ---------------------------------------------------------------------------


class TestUpdateSaga:
    def test_pr_appended(self) -> None:
        store = FakeStore()
        saga = _make_saga(pr_numbers=[1, 2])

        updated = update_saga(store, saga, pr_number=3)

        assert 3 in updated.pr_numbers
        assert updated.pr_numbers == [1, 2, 3]

    def test_idempotent(self) -> None:
        store = FakeStore()
        saga = _make_saga(pr_numbers=[1])

        updated = update_saga(store, saga, pr_number=1)

        assert updated.pr_numbers == [1]

    def test_saga_file_persisted(self) -> None:
        store = FakeStore()
        saga = _make_saga(saga_id="auth-overhaul", pr_numbers=[])

        update_saga(store, saga, pr_number=99)

        assert store.exists("memory/sagas/auth-overhaul.md")
        raw = store.read("memory/sagas/auth-overhaul.md")
        assert "99" in raw

    def test_index_updated(self) -> None:
        store = FakeStore()
        # Seed the index with the saga
        store.write_json(
            "memory/sagas/_index.json",
            {
                "sagas": [
                    {
                        "id": "auth-overhaul",
                        "name": "The Authentication Overhaul",
                        "start_date": "2026-01-01T00:00:00+00:00",
                        "end_date": None,
                        "status": "active",
                        "pr_numbers": [],
                        "description": "",
                    }
                ]
            },
        )
        saga = _make_saga(saga_id="auth-overhaul", pr_numbers=[])

        update_saga(store, saga, pr_number=5)

        index = store.read_json("memory/sagas/_index.json")
        entry = next(e for e in index["sagas"] if e["id"] == "auth-overhaul")
        assert 5 in entry["pr_numbers"]

    def test_index_created_if_missing(self) -> None:
        store = FakeStore()
        saga = _make_saga(saga_id="fresh-saga", pr_numbers=[])

        update_saga(store, saga, pr_number=7)

        assert store.exists("memory/sagas/_index.json")
        index = store.read_json("memory/sagas/_index.json")
        ids = [e["id"] for e in index["sagas"]]
        assert "fresh-saga" in ids


# ---------------------------------------------------------------------------
# read_chronicle
# ---------------------------------------------------------------------------


def _seed_journal(store: FakeStore, entries: list[JournalEntry]) -> None:
    """Write pre-baked journal entries into the FakeStore."""
    for entry in entries:
        date_str = entry.timestamp.strftime("%Y-%m-%d")
        path = f"memory/journal/{date_str}-pr-{entry.pr_number}.md"
        ts_str = entry.timestamp.strftime("%Y-%m-%d %H:%M:%S UTC")
        content = "\n".join(
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
        )
        store.write(path, content)


class TestReadChronicle:
    def _sample_entries(self) -> list[JournalEntry]:
        return [
            JournalEntry(
                pr_number=1,
                timestamp=datetime(2026, 1, 10, tzinfo=UTC),
                saga_id="saga-a",
                verdict=Verdict.ALIGNED,
                body_markdown="First PR body.",
            ),
            JournalEntry(
                pr_number=2,
                timestamp=datetime(2026, 2, 1, tzinfo=UTC),
                saga_id="saga-b",
                verdict=Verdict.DRIFT,
                body_markdown="Second PR body.",
            ),
            JournalEntry(
                pr_number=3,
                timestamp=datetime(2026, 3, 15, tzinfo=UTC),
                saga_id="saga-a",
                verdict=Verdict.AMBIGUOUS,
                body_markdown="Third PR body.",
            ),
        ]

    def test_returns_all_entries_no_filters(self) -> None:
        store = FakeStore()
        _seed_journal(store, self._sample_entries())

        result = read_chronicle(store)

        assert len(result) == 3

    def test_filter_since(self) -> None:
        store = FakeStore()
        _seed_journal(store, self._sample_entries())

        cutoff = datetime(2026, 1, 15, tzinfo=UTC)
        result = read_chronicle(store, since=cutoff)

        assert all(e.pr_number != 1 for e in result)
        assert len(result) == 2

    def test_filter_saga_id(self) -> None:
        store = FakeStore()
        _seed_journal(store, self._sample_entries())

        result = read_chronicle(store, saga_id="saga-a")

        assert all(e.saga_id == "saga-a" for e in result)
        assert len(result) == 2

    def test_filter_drift_only(self) -> None:
        store = FakeStore()
        _seed_journal(store, self._sample_entries())

        result = read_chronicle(store, drift_only=True)

        assert all(e.verdict == Verdict.DRIFT for e in result)
        assert len(result) == 1
        assert result[0].pr_number == 2

    def test_combined_filters(self) -> None:
        store = FakeStore()
        _seed_journal(store, self._sample_entries())

        cutoff = datetime(2026, 1, 15, tzinfo=UTC)
        result = read_chronicle(store, since=cutoff, saga_id="saga-a")

        # Only PR #3 is after cutoff AND in saga-a
        assert len(result) == 1
        assert result[0].pr_number == 3

    def test_empty_store_returns_empty_list(self) -> None:
        store = FakeStore()
        result = read_chronicle(store)
        assert result == []

    def test_entries_sorted_alphabetically_by_filename(self) -> None:
        store = FakeStore()
        _seed_journal(store, self._sample_entries())

        result = read_chronicle(store)

        pr_numbers = [e.pr_number for e in result]
        assert pr_numbers == sorted(pr_numbers), "Entries should be in filename order"
