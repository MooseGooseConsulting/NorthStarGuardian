"""Tests for guardian.north_star — North Star I/O and amendment logic.

Uses FakeStore (from conftest.py) so no git or network activity occurs.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from guardian.models import Amendment, AntiPattern, NorthStar, Principle
from guardian.north_star import (
    amend_north_star,
    append_amendment,
    initialize_north_star,
    parse_north_star_markdown,
    read_north_star,
    render_north_star_markdown,
    write_north_star,
)
from tests.conftest import FakeStore

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_NOW = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)


def _sample_north_star() -> NorthStar:
    return NorthStar(
        version=1,
        project_name="TestProject",
        identity_statement="This is a test project. It is not a toy.",
        principles=[
            Principle(id="p1", rank=1, text="All data flows through the LLM layer"),
            Principle(id="p2", rank=2, text="Local-first always", rationale="No SaaS deps"),
        ],
        approved_architecture="We use FastAPI, SQLAlchemy, and LangChain.",
        anti_patterns=[
            AntiPattern(
                id="ap1",
                description="Raw CSV scripts that bypass the LLM",
                example="analyze.py reading a CSV directly",
                detect="import csv",
            )
        ],
        created_at=_NOW,
    )


# ---------------------------------------------------------------------------
# Markdown round-trip
# ---------------------------------------------------------------------------


class TestMarkdownRoundTrip:
    def test_render_and_parse_identity(self) -> None:
        c = _sample_north_star()
        md = render_north_star_markdown(c)
        parsed = parse_north_star_markdown(md)
        assert parsed.identity_statement == c.identity_statement

    def test_render_and_parse_project_name(self) -> None:
        c = _sample_north_star()
        md = render_north_star_markdown(c)
        parsed = parse_north_star_markdown(md)
        assert parsed.project_name == c.project_name

    def test_render_and_parse_principles(self) -> None:
        c = _sample_north_star()
        md = render_north_star_markdown(c)
        parsed = parse_north_star_markdown(md)
        assert len(parsed.principles) == 2
        assert parsed.principles[0].id == "p1"
        assert parsed.principles[0].text == "All data flows through the LLM layer"
        assert parsed.principles[1].rationale == "No SaaS deps"

    def test_render_and_parse_anti_patterns(self) -> None:
        c = _sample_north_star()
        md = render_north_star_markdown(c)
        parsed = parse_north_star_markdown(md)
        assert len(parsed.anti_patterns) == 1
        assert parsed.anti_patterns[0].id == "ap1"
        assert parsed.anti_patterns[0].example == "analyze.py reading a CSV directly"
        assert parsed.anti_patterns[0].detect == "import csv"

    def test_render_and_parse_version(self) -> None:
        c = _sample_north_star()
        md = render_north_star_markdown(c)
        parsed = parse_north_star_markdown(md)
        assert parsed.version == 1

    def test_render_and_parse_architecture(self) -> None:
        c = _sample_north_star()
        md = render_north_star_markdown(c)
        parsed = parse_north_star_markdown(md)
        assert "FastAPI" in parsed.approved_architecture

    def test_render_and_parse_amended_at(self) -> None:
        c = _sample_north_star()
        amended = c.model_copy(update={"amended_at": _NOW})
        md = render_north_star_markdown(amended)
        parsed = parse_north_star_markdown(md)
        assert parsed.amended_at is not None

    def test_parse_missing_frontmatter_raises(self) -> None:
        with pytest.raises(ValueError, match="frontmatter"):
            parse_north_star_markdown("# No frontmatter here\n")

    def test_render_contains_project_name_heading(self) -> None:
        c = _sample_north_star()
        md = render_north_star_markdown(c)
        assert "# TestProject" in md

    def test_render_contains_each_principle(self) -> None:
        c = _sample_north_star()
        md = render_north_star_markdown(c)
        assert "All data flows through the LLM layer" in md
        assert "Local-first always" in md


# ---------------------------------------------------------------------------
# Read / write via FakeStore
# ---------------------------------------------------------------------------


class TestReadWrite:
    def test_write_and_read_north_star(self, fake_store: FakeStore) -> None:
        c = _sample_north_star()
        write_north_star(fake_store, c)
        loaded = read_north_star(fake_store)
        assert loaded.project_name == c.project_name
        assert loaded.version == c.version

    def test_read_missing_raises(self, fake_store: FakeStore) -> None:
        with pytest.raises(FileNotFoundError):
            read_north_star(fake_store)

    def test_write_stages_to_store(self, fake_store: FakeStore) -> None:
        c = _sample_north_star()
        write_north_star(fake_store, c)
        assert fake_store.exists("northstar.md")


# ---------------------------------------------------------------------------
# amend_north_star
# ---------------------------------------------------------------------------


class TestAmendNorthStar:
    def _store_with_north_star(self, fake_store: FakeStore) -> FakeStore:
        write_north_star(fake_store, _sample_north_star())
        return fake_store

    def test_amend_identity(self, fake_store: FakeStore) -> None:
        self._store_with_north_star(fake_store)
        updated = amend_north_star(
            fake_store,
            target="identity",
            target_id=None,
            after="Revised identity statement.",
            rationale="Scope changed",
            actor="alice",
        )
        assert updated.identity_statement == "Revised identity statement."

    def test_amend_identity_bumps_version(self, fake_store: FakeStore) -> None:
        self._store_with_north_star(fake_store)
        updated = amend_north_star(
            fake_store,
            target="identity",
            target_id=None,
            after="New identity.",
            rationale="r",
            actor="alice",
        )
        assert updated.version == 2

    def test_amend_principle_text(self, fake_store: FakeStore) -> None:
        self._store_with_north_star(fake_store)
        updated = amend_north_star(
            fake_store,
            target="principle",
            target_id="p1",
            after="Revised principle one text",
            rationale="Clarified wording",
            actor="bob",
        )
        p1 = next(p for p in updated.principles if p.id == "p1")
        assert p1.text == "Revised principle one text"

    def test_amend_principle_missing_id_raises(self, fake_store: FakeStore) -> None:
        self._store_with_north_star(fake_store)
        with pytest.raises(ValueError, match="target_id is required"):
            amend_north_star(
                fake_store,
                target="principle",
                target_id=None,
                after="x",
                rationale="r",
                actor="alice",
            )

    def test_amend_principle_unknown_id_raises(self, fake_store: FakeStore) -> None:
        self._store_with_north_star(fake_store)
        with pytest.raises(ValueError, match="not found"):
            amend_north_star(
                fake_store,
                target="principle",
                target_id="p99",
                after="x",
                rationale="r",
                actor="alice",
            )

    def test_amend_anti_pattern(self, fake_store: FakeStore) -> None:
        self._store_with_north_star(fake_store)
        updated = amend_north_star(
            fake_store,
            target="anti_pattern",
            target_id="ap1",
            after="Updated anti-pattern description",
            rationale="More precise",
            actor="carol",
        )
        ap1 = next(a for a in updated.anti_patterns if a.id == "ap1")
        assert ap1.description == "Updated anti-pattern description"

    def test_amend_architecture(self, fake_store: FakeStore) -> None:
        self._store_with_north_star(fake_store)
        updated = amend_north_star(
            fake_store,
            target="architecture",
            target_id=None,
            after="We now use FastAPI, SQLAlchemy, LangChain, and Redis.",
            rationale="Added caching layer",
            actor="dave",
        )
        assert "Redis" in updated.approved_architecture

    def test_amend_unknown_target_raises(self, fake_store: FakeStore) -> None:
        self._store_with_north_star(fake_store)
        with pytest.raises(ValueError, match="Unknown amendment target"):
            amend_north_star(
                fake_store,
                target="unknown_field",
                target_id=None,
                after="x",
                rationale="r",
                actor="alice",
            )

    def test_amend_sets_amended_at(self, fake_store: FakeStore) -> None:
        self._store_with_north_star(fake_store)
        updated = amend_north_star(
            fake_store,
            target="identity",
            target_id=None,
            after="New identity.",
            rationale="r",
            actor="alice",
        )
        assert updated.amended_at is not None

    def test_amend_persists_to_store(self, fake_store: FakeStore) -> None:
        self._store_with_north_star(fake_store)
        amend_north_star(
            fake_store,
            target="identity",
            target_id=None,
            after="Persisted identity.",
            rationale="r",
            actor="alice",
        )
        reloaded = read_north_star(fake_store)
        assert reloaded.identity_statement == "Persisted identity."


# ---------------------------------------------------------------------------
# append_amendment / amendment log
# ---------------------------------------------------------------------------


class TestAmendmentLog:
    def _make_amendment(self) -> Amendment:
        return Amendment(
            timestamp=_NOW,
            actor="alice",
            target="identity",
            target_id=None,
            before="Old identity.",
            after="New identity.",
            rationale="Scope changed",
        )

    def test_append_creates_log(self, fake_store: FakeStore) -> None:
        append_amendment(fake_store, self._make_amendment())
        assert fake_store.exists("memory/amendment-log.md")

    def test_append_contains_actor(self, fake_store: FakeStore) -> None:
        append_amendment(fake_store, self._make_amendment())
        log = fake_store.read("memory/amendment-log.md")
        assert "alice" in log

    def test_append_contains_rationale(self, fake_store: FakeStore) -> None:
        append_amendment(fake_store, self._make_amendment())
        log = fake_store.read("memory/amendment-log.md")
        assert "Scope changed" in log

    def test_append_is_cumulative(self, fake_store: FakeStore) -> None:
        a1 = self._make_amendment()
        a2 = Amendment(
            timestamp=_NOW,
            actor="bob",
            target="principle",
            target_id="p1",
            before="Old principle.",
            after="New principle.",
            rationale="Clearer wording",
        )
        append_amendment(fake_store, a1)
        append_amendment(fake_store, a2)
        log = fake_store.read("memory/amendment-log.md")
        assert "alice" in log
        assert "bob" in log


# ---------------------------------------------------------------------------
# initialize_north_star
# ---------------------------------------------------------------------------


class TestInitializeNorthStar:
    def _answers(self) -> dict:
        return {
            "project_name": "MyProject",
            "identity_statement": "An LLM-powered analysis pipeline.",
            "principles": [
                "All analysis flows through the LLM",
                "Local-first always",
                "No external SaaS",
            ],
            "approved_architecture": "FastAPI + LangChain",
            "anti_patterns": [
                {"id": "ap1", "description": "Rogue CSV scripts", "example": "analyze.py"},
            ],
        }

    def test_returns_north_star(self) -> None:
        c = initialize_north_star(self._answers(), actor="alice")
        assert isinstance(c, NorthStar)

    def test_project_name(self) -> None:
        c = initialize_north_star(self._answers(), actor="alice")
        assert c.project_name == "MyProject"

    def test_principles_from_strings(self) -> None:
        c = initialize_north_star(self._answers(), actor="alice")
        assert len(c.principles) == 3
        assert c.principles[0].text == "All analysis flows through the LLM"
        assert c.principles[0].id == "p1"
        assert c.principles[0].rank == 1

    def test_principles_from_dicts(self) -> None:
        answers = self._answers()
        answers["principles"] = [
            {"id": "custom-1", "rank": 1, "text": "LLM-first", "rationale": "Core value"},
        ]
        c = initialize_north_star(answers, actor="alice")
        assert c.principles[0].id == "custom-1"
        assert c.principles[0].rationale == "Core value"

    def test_anti_patterns_from_dicts(self) -> None:
        c = initialize_north_star(self._answers(), actor="alice")
        assert len(c.anti_patterns) == 1
        assert c.anti_patterns[0].id == "ap1"
        assert c.anti_patterns[0].example == "analyze.py"

    def test_anti_patterns_from_strings(self) -> None:
        answers = self._answers()
        answers["anti_patterns"] = ["Rogue scripts", "Inline SQL"]
        c = initialize_north_star(answers, actor="alice")
        assert len(c.anti_patterns) == 2
        assert c.anti_patterns[0].description == "Rogue scripts"
        assert c.anti_patterns[0].id == "ap1"

    def test_version_starts_at_one(self) -> None:
        c = initialize_north_star(self._answers(), actor="alice")
        assert c.version == 1

    def test_created_at_is_set(self) -> None:
        c = initialize_north_star(self._answers(), actor="alice")
        assert c.created_at is not None

    def test_no_anti_patterns_key(self) -> None:
        answers = self._answers()
        del answers["anti_patterns"]
        c = initialize_north_star(answers, actor="alice")
        assert c.anti_patterns == []
