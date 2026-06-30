"""Tests for guardian/governance.py.

All tests run fully offline — no network, no LLM.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from guardian.governance import (
    check_debt_timers,
    escalate_debt,
    escalate_debt_result,
    grant_variance,
    log_drift,
    resolve_debt,
)
from guardian.models import (
    DebtLevel,
    DriftSeverity,
    GuardianConfig,
    VarianceTag,
)
from tests.helpers import FakeStore


@pytest.fixture()
def store() -> FakeStore:
    return FakeStore()


@pytest.fixture()
def config() -> GuardianConfig:
    return GuardianConfig()  # defaults: enable_blocking_escalation=False, variance_default_days=7


@pytest.fixture()
def blocking_config() -> GuardianConfig:
    return GuardianConfig(enable_blocking_escalation=True)


def _make_variance_tag(
    principle_id: str = "P1",
    justification: str = "hotfix for production",
    expires_in_days: int = 7,
) -> VarianceTag:
    return VarianceTag(
        principle_id=principle_id,
        justification=justification,
        expires_in_days=expires_in_days,
        raw=f"[VARIANCE: {principle_id} — {justification}, {expires_in_days} days]",
    )


def _fixed_now() -> datetime:
    return datetime(2026, 5, 22, 12, 0, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# log_drift
# ---------------------------------------------------------------------------


class TestLogDrift:
    def test_returns_drift_event(self, store: FakeStore, config: GuardianConfig) -> None:
        event = log_drift(
            store,
            pr_number=42,
            principle_id="P1",
            severity=DriftSeverity.HIGH,
            details="Whisper imported directly.",
            now=_fixed_now(),
        )
        assert event.pr_number == 42
        assert event.principle_id == "P1"
        assert event.severity == DriftSeverity.HIGH
        assert event.details == "Whisper imported directly."
        assert event.id != ""

    def test_persists_to_store(self, store: FakeStore) -> None:
        log_drift(
            store,
            pr_number=10,
            principle_id="P2",
            severity=DriftSeverity.MEDIUM,
            details="Test drift.",
            now=_fixed_now(),
        )
        assert store.exists("memory/drift-ledger.json")
        ledger = store.read_json("memory/drift-ledger.json")
        assert len(ledger) == 1
        assert ledger[0]["pr_number"] == 10

    def test_appends_multiple_events(self, store: FakeStore) -> None:
        log_drift(
            store, pr_number=1, principle_id="P1", severity=DriftSeverity.LOW, details="First"
        )
        log_drift(
            store, pr_number=2, principle_id="P2", severity=DriftSeverity.HIGH, details="Second"
        )
        ledger = store.read_json("memory/drift-ledger.json")
        assert len(ledger) == 2

    def test_accepts_string_severity(self, store: FakeStore) -> None:
        event = log_drift(
            store,
            pr_number=5,
            principle_id="P3",
            severity="low",  # string form
            details="String severity.",
        )
        assert event.severity == DriftSeverity.LOW

    def test_event_has_unique_ids(self, store: FakeStore) -> None:
        e1 = log_drift(
            store, pr_number=1, principle_id="P1", severity=DriftSeverity.LOW, details="A"
        )
        e2 = log_drift(
            store, pr_number=2, principle_id="P2", severity=DriftSeverity.LOW, details="B"
        )
        assert e1.id != e2.id

    def test_timestamp_stored_as_iso(self, store: FakeStore) -> None:
        log_drift(
            store,
            pr_number=1,
            principle_id="P1",
            severity=DriftSeverity.LOW,
            details="ts test",
            now=_fixed_now(),
        )
        ledger = store.read_json("memory/drift-ledger.json")
        assert "2026-05-22" in ledger[0]["timestamp"]


# ---------------------------------------------------------------------------
# grant_variance
# ---------------------------------------------------------------------------


class TestGrantVariance:
    def test_returns_debt_timer(self, store: FakeStore, config: GuardianConfig) -> None:
        tag = _make_variance_tag(expires_in_days=7)
        timer = grant_variance(store, tag, pr_number=42, config=config, now=_fixed_now())
        assert timer.pr_number == 42
        assert timer.principle_id == "P1"
        assert timer.level == DebtLevel.NEUTRAL
        assert timer.resolved_at is None

    def test_expiry_calculated_from_tag(self, store: FakeStore, config: GuardianConfig) -> None:
        tag = _make_variance_tag(expires_in_days=14)
        now = _fixed_now()
        timer = grant_variance(store, tag, pr_number=1, config=config, now=now)
        expected_expiry = now + timedelta(days=14)
        assert timer.expires_at == expected_expiry

    def test_fallback_to_config_default_days(
        self, store: FakeStore, config: GuardianConfig
    ) -> None:
        """When tag.expires_in_days == 0, config.variance_default_days is used."""
        tag = _make_variance_tag(expires_in_days=0)
        now = _fixed_now()
        timer = grant_variance(store, tag, pr_number=1, config=config, now=now)
        expected_expiry = now + timedelta(days=config.variance_default_days)
        assert timer.expires_at == expected_expiry

    def test_negative_days_falls_back_to_default(
        self, store: FakeStore, config: GuardianConfig
    ) -> None:
        tag = _make_variance_tag(expires_in_days=-3)
        now = _fixed_now()
        timer = grant_variance(store, tag, pr_number=1, config=config, now=now)
        expected_expiry = now + timedelta(days=config.variance_default_days)
        assert timer.expires_at == expected_expiry

    def test_persists_to_store(self, store: FakeStore, config: GuardianConfig) -> None:
        tag = _make_variance_tag()
        grant_variance(store, tag, pr_number=42, config=config, now=_fixed_now())
        assert store.exists("memory/debt-timers.json")
        timers = store.read_json("memory/debt-timers.json")
        assert len(timers) == 1

    def test_affected_paths_stored(self, store: FakeStore, config: GuardianConfig) -> None:
        tag = _make_variance_tag()
        timer = grant_variance(
            store,
            tag,
            pr_number=1,
            config=config,
            affected_paths=["src/foo.py", "src/bar.py"],
        )
        assert timer.affected_paths == ["src/foo.py", "src/bar.py"]
        raw = store.read_json("memory/debt-timers.json")
        assert raw[0]["affected_paths"] == ["src/foo.py", "src/bar.py"]

    def test_multiple_timers_append(self, store: FakeStore, config: GuardianConfig) -> None:
        tag1 = _make_variance_tag("P1")
        tag2 = _make_variance_tag("P2")
        grant_variance(store, tag1, pr_number=1, config=config)
        grant_variance(store, tag2, pr_number=2, config=config)
        timers = store.read_json("memory/debt-timers.json")
        assert len(timers) == 2


# ---------------------------------------------------------------------------
# check_debt_timers
# ---------------------------------------------------------------------------


class TestCheckDebtTimers:
    def _create_timer(
        self,
        store: FakeStore,
        config: GuardianConfig,
        *,
        expires_in_days: int,
        principle_id: str = "P1",
        pr_number: int = 1,
        now: datetime | None = None,
    ) -> None:
        tag = _make_variance_tag(principle_id=principle_id, expires_in_days=expires_in_days)
        grant_variance(store, tag, pr_number=pr_number, config=config, now=now or _fixed_now())

    def test_empty_store_returns_empty_buckets(self, store: FakeStore) -> None:
        result = check_debt_timers(store, now=_fixed_now())
        assert result["active"] == []
        assert result["approaching_expiry"] == []
        assert result["expired"] == []

    def test_active_timer(self, store: FakeStore, config: GuardianConfig) -> None:
        self._create_timer(store, config, expires_in_days=10)
        # Check at creation time — only 0% elapsed, so it's active
        result = check_debt_timers(store, now=_fixed_now())
        assert len(result["active"]) == 1
        assert result["approaching_expiry"] == []
        assert result["expired"] == []

    def test_active_at_50_percent(self, store: FakeStore, config: GuardianConfig) -> None:
        # Timer created 10 days ago with a 20-day lifespan (expires in 10 more days)
        # At check time: elapsed = 10/20 = 50% → below the 75% threshold → active
        created = _fixed_now() - timedelta(days=10)
        self._create_timer(store, config, expires_in_days=20, now=created)
        result = check_debt_timers(store, now=_fixed_now())
        assert len(result["active"]) == 1

    def test_approaching_expiry_at_80_percent(
        self, store: FakeStore, config: GuardianConfig
    ) -> None:
        # 10-day timer, check at day 8 → 80% elapsed → approaching
        created = _fixed_now()
        tag = _make_variance_tag(expires_in_days=10)
        grant_variance(store, tag, pr_number=1, config=config, now=created)
        check_time = created + timedelta(days=8)
        result = check_debt_timers(store, now=check_time)
        assert len(result["approaching_expiry"]) == 1
        assert result["active"] == []
        assert result["expired"] == []

    def test_expired_timer(self, store: FakeStore, config: GuardianConfig) -> None:
        created = _fixed_now() - timedelta(days=20)
        self._create_timer(store, config, expires_in_days=7, now=created)
        result = check_debt_timers(store, now=_fixed_now())
        assert len(result["expired"]) == 1
        assert result["active"] == []

    def test_resolved_timer_excluded(self, store: FakeStore, config: GuardianConfig) -> None:
        tag = _make_variance_tag(expires_in_days=7)
        timer = grant_variance(store, tag, pr_number=1, config=config, now=_fixed_now())
        resolve_debt(store, timer.id)
        result = check_debt_timers(store, now=_fixed_now())
        assert result["active"] == []
        assert result["approaching_expiry"] == []
        assert result["expired"] == []

    def test_multiple_timers_classified_correctly(
        self, store: FakeStore, config: GuardianConfig
    ) -> None:
        now = _fixed_now()

        # Active: 10-day timer just created
        tag_a = _make_variance_tag("P1", expires_in_days=10)
        grant_variance(store, tag_a, pr_number=1, config=config, now=now)

        # Approaching: 10-day timer, at day 9
        tag_b = _make_variance_tag("P2", expires_in_days=10)
        grant_variance(store, tag_b, pr_number=2, config=config, now=now - timedelta(days=9))

        # Expired: 7-day timer created 14 days ago
        tag_c = _make_variance_tag("P3", expires_in_days=7)
        grant_variance(store, tag_c, pr_number=3, config=config, now=now - timedelta(days=14))

        result = check_debt_timers(store, now=now)
        assert len(result["active"]) == 1
        assert len(result["approaching_expiry"]) == 1
        assert len(result["expired"]) == 1


# ---------------------------------------------------------------------------
# escalate_debt
# ---------------------------------------------------------------------------


class TestEscalateDebt:
    def _create_timer_and_get_id(self, store: FakeStore, config: GuardianConfig) -> str:
        tag = _make_variance_tag()
        timer = grant_variance(store, tag, pr_number=1, config=config, now=_fixed_now())
        return timer.id

    def test_bumps_level(self, store: FakeStore, config: GuardianConfig) -> None:
        debt_id = self._create_timer_and_get_id(store, config)
        updated = escalate_debt(store, debt_id, new_level=DebtLevel.REMINDER_75, config=config)
        assert updated.level == DebtLevel.REMINDER_75

    def test_persists_level_change(self, store: FakeStore, config: GuardianConfig) -> None:
        debt_id = self._create_timer_and_get_id(store, config)
        escalate_debt(store, debt_id, new_level=DebtLevel.REMINDER_EXPIRED, config=config)
        timers = store.read_json("memory/debt-timers.json")
        updated = next(t for t in timers if t["id"] == debt_id)
        assert updated["level"] == int(DebtLevel.REMINDER_EXPIRED)

    def test_blocking_level_recorded_even_without_flag(
        self, store: FakeStore, config: GuardianConfig
    ) -> None:
        """BLOCKING level is always recorded in the model regardless of the flag."""
        debt_id = self._create_timer_and_get_id(store, config)
        updated = escalate_debt(store, debt_id, new_level=DebtLevel.BLOCKING, config=config)
        assert updated.level == DebtLevel.BLOCKING

    def test_unknown_id_raises_key_error(self, store: FakeStore, config: GuardianConfig) -> None:
        with pytest.raises(KeyError):
            escalate_debt(store, "nonexistent-id", new_level=DebtLevel.REMINDER_75, config=config)


class TestEscalateDebtResult:
    def _create_timer_and_get_id(self, store: FakeStore, config: GuardianConfig) -> str:
        tag = _make_variance_tag()
        timer = grant_variance(store, tag, pr_number=1, config=config, now=_fixed_now())
        return timer.id

    def test_blocks_pr_false_when_flag_disabled(
        self, store: FakeStore, config: GuardianConfig
    ) -> None:
        """With enable_blocking_escalation=False (default), blocks_pr must be False."""
        debt_id = self._create_timer_and_get_id(store, config)
        _, blocks_pr = escalate_debt_result(
            store, debt_id, new_level=DebtLevel.BLOCKING, config=config
        )
        assert blocks_pr is False

    def test_blocks_pr_true_when_flag_enabled(
        self, store: FakeStore, blocking_config: GuardianConfig
    ) -> None:
        """With enable_blocking_escalation=True, BLOCKING level returns blocks_pr=True."""
        debt_id = self._create_timer_and_get_id(store, blocking_config)
        _, blocks_pr = escalate_debt_result(
            store, debt_id, new_level=DebtLevel.BLOCKING, config=blocking_config
        )
        assert blocks_pr is True

    def test_blocks_pr_false_for_non_blocking_level(
        self, store: FakeStore, blocking_config: GuardianConfig
    ) -> None:
        debt_id = self._create_timer_and_get_id(store, blocking_config)
        _, blocks_pr = escalate_debt_result(
            store, debt_id, new_level=DebtLevel.REMINDER_75, config=blocking_config
        )
        assert blocks_pr is False

    def test_timer_level_recorded_regardless_of_flag(
        self, store: FakeStore, config: GuardianConfig
    ) -> None:
        """Even with flag=False, the BLOCKING level is still written to the store."""
        debt_id = self._create_timer_and_get_id(store, config)
        timer, _ = escalate_debt_result(store, debt_id, new_level=DebtLevel.BLOCKING, config=config)
        assert timer.level == DebtLevel.BLOCKING
        raw = store.read_json("memory/debt-timers.json")
        persisted = next(t for t in raw if t["id"] == debt_id)
        assert persisted["level"] == int(DebtLevel.BLOCKING)


# ---------------------------------------------------------------------------
# resolve_debt
# ---------------------------------------------------------------------------


class TestResolveDebt:
    def _create_timer(self, store: FakeStore, config: GuardianConfig) -> str:
        tag = _make_variance_tag()
        timer = grant_variance(store, tag, pr_number=1, config=config, now=_fixed_now())
        return timer.id

    def test_sets_resolved_at(self, store: FakeStore, config: GuardianConfig) -> None:
        debt_id = self._create_timer(store, config)
        resolved_time = _fixed_now() + timedelta(days=3)
        updated = resolve_debt(store, debt_id, now=resolved_time)
        assert updated.resolved_at == resolved_time

    def test_persists_resolved_at(self, store: FakeStore, config: GuardianConfig) -> None:
        debt_id = self._create_timer(store, config)
        resolve_debt(store, debt_id)
        raw = store.read_json("memory/debt-timers.json")
        timer_raw = next(t for t in raw if t["id"] == debt_id)
        assert timer_raw["resolved_at"] is not None

    def test_resolved_pr_appended_to_justification(
        self, store: FakeStore, config: GuardianConfig
    ) -> None:
        debt_id = self._create_timer(store, config)
        updated = resolve_debt(store, debt_id, resolved_pr=99)
        assert "PR #99" in updated.justification

    def test_unknown_id_raises_key_error(self, store: FakeStore, config: GuardianConfig) -> None:
        with pytest.raises(KeyError):
            resolve_debt(store, "nonexistent-id")

    def test_resolved_timer_excluded_from_check(
        self, store: FakeStore, config: GuardianConfig
    ) -> None:
        debt_id = self._create_timer(store, config)
        resolve_debt(store, debt_id)
        result = check_debt_timers(store, now=_fixed_now())
        assert all(len(bucket) == 0 for bucket in result.values())

    def test_resolve_does_not_affect_other_timers(
        self, store: FakeStore, config: GuardianConfig
    ) -> None:
        tag1 = _make_variance_tag("P1")
        tag2 = _make_variance_tag("P2")
        t1 = grant_variance(store, tag1, pr_number=1, config=config, now=_fixed_now())
        grant_variance(store, tag2, pr_number=2, config=config, now=_fixed_now())
        resolve_debt(store, t1.id)
        result = check_debt_timers(store, now=_fixed_now())
        # t2 is still active
        assert len(result["active"]) == 1
        assert result["active"][0].principle_id == "P2"
