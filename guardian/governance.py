"""Governance tools for NorthStarGuardian.

Implements:
    log_drift         — append a DriftEvent to memory/drift-ledger.json
    grant_variance    — create a DebtTimer from a VarianceTag
    check_debt_timers — classify active timers by escalation level
    escalate_debt     — bump a timer's DebtLevel
    resolve_debt      — mark a timer resolved

State lives in JSON files under ``.github/guardian/memory`` through a
MemoryStore instance.  Functions perform read-modify-write; callers are
responsible for concurrency if needed.

Blocking-escalation behavior is gated by ``GuardianConfig.enable_blocking_escalation``
(default False).  The escalation machinery always records the level change; the
merge-block side effect (``blocks_pr=True``) is only returned when the flag is
enabled, honoring the North Star anti-goal that the Guardian must not become an
authority by default.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from guardian.models import (
    DebtLevel,
    DebtTimer,
    DriftEvent,
    DriftSeverity,
    GuardianConfig,
    VarianceTag,
)

if TYPE_CHECKING:
    # Imported by signature; the memory module is owned by another agent.
    pass

# ---------------------------------------------------------------------------
# File paths within the MemoryStore
# ---------------------------------------------------------------------------

_DRIFT_LEDGER = "memory/drift-ledger.json"
_DRIFT_LEDGER_LEGACY = "drift-ledger.json"
_DEBT_TIMERS = "memory/debt-timers.json"
_DEBT_TIMERS_LEGACY = "debt-timers.json"

# Threshold: timers beyond this fraction of their lifespan get a 75% reminder.
_REMINDER_75_FRACTION = 0.75


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _now_utc(override: datetime | None) -> datetime:
    return override if override is not None else datetime.now(tz=UTC)


def _ensure_aware(dt: datetime) -> datetime:
    """Return *dt* as a timezone-aware UTC datetime."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt


def _load_drift_ledger(store: Any) -> list[dict[str, Any]]:
    if store.exists(_DRIFT_LEDGER):
        data = store.read_json(_DRIFT_LEDGER)
        return data if isinstance(data, list) else []
    if store.exists(_DRIFT_LEDGER_LEGACY):
        data = store.read_json(_DRIFT_LEDGER_LEGACY)
        return data if isinstance(data, list) else []
    return []


def _save_drift_ledger(store: Any, ledger: list[dict[str, Any]]) -> None:
    store.write_json(_DRIFT_LEDGER, ledger)


def _load_debt_timers(store: Any) -> list[dict[str, Any]]:
    if store.exists(_DEBT_TIMERS):
        data = store.read_json(_DEBT_TIMERS)
        return data if isinstance(data, list) else []
    if store.exists(_DEBT_TIMERS_LEGACY):
        data = store.read_json(_DEBT_TIMERS_LEGACY)
        return data if isinstance(data, list) else []
    return []


def _save_debt_timers(store: Any, timers: list[dict[str, Any]]) -> None:
    store.write_json(_DEBT_TIMERS, timers)


def _timer_from_dict(d: dict[str, Any]) -> DebtTimer:
    """Deserialize a DebtTimer from its JSON dict representation."""
    return DebtTimer(
        id=d["id"],
        pr_number=int(d["pr_number"]),
        principle_id=str(d["principle_id"]),
        justification=str(d["justification"]),
        created_at=_ensure_aware(datetime.fromisoformat(d["created_at"])),
        expires_at=_ensure_aware(datetime.fromisoformat(d["expires_at"])),
        resolved_at=(
            _ensure_aware(datetime.fromisoformat(d["resolved_at"]))
            if d.get("resolved_at")
            else None
        ),
        level=DebtLevel(int(d.get("level", DebtLevel.NEUTRAL))),
        affected_paths=list(d.get("affected_paths", [])),
    )


def _timer_to_dict(timer: DebtTimer) -> dict[str, Any]:
    """Serialize a DebtTimer to a JSON-safe dict."""
    return {
        "id": timer.id,
        "pr_number": timer.pr_number,
        "principle_id": timer.principle_id,
        "justification": timer.justification,
        "created_at": timer.created_at.isoformat(),
        "expires_at": timer.expires_at.isoformat(),
        "resolved_at": timer.resolved_at.isoformat() if timer.resolved_at else None,
        "level": int(timer.level),
        "affected_paths": timer.affected_paths,
    }


def _drift_from_dict(d: dict[str, Any]) -> DriftEvent:
    return DriftEvent(
        id=d["id"],
        pr_number=int(d["pr_number"]),
        principle_id=str(d["principle_id"]),
        severity=DriftSeverity(d["severity"]),
        details=str(d["details"]),
        timestamp=_ensure_aware(datetime.fromisoformat(d["timestamp"])),
    )


def _drift_to_dict(event: DriftEvent) -> dict[str, Any]:
    return {
        "id": event.id,
        "pr_number": event.pr_number,
        "principle_id": event.principle_id,
        "severity": event.severity.value,
        "details": event.details,
        "timestamp": event.timestamp.isoformat(),
    }


# ---------------------------------------------------------------------------
# log_drift
# ---------------------------------------------------------------------------


def log_drift(
    store: Any,
    *,
    pr_number: int,
    principle_id: str,
    severity: DriftSeverity | str,
    details: str,
    now: datetime | None = None,
) -> DriftEvent:
    """Append a drift event to ``memory/drift-ledger.json`` and return it.

    Parameters
    ----------
    store:
        A ``MemoryStore`` instance.
    pr_number:
        The PR that introduced the drift.
    principle_id:
        The id of the violated principle.
    severity:
        A :class:`DriftSeverity` value or its string equivalent.
    details:
        Human-readable description of the drift.
    now:
        Timestamp override for testing; defaults to current UTC time.

    Returns
    -------
    DriftEvent
    """
    if isinstance(severity, str):
        severity = DriftSeverity(severity)

    event = DriftEvent(
        id=str(uuid.uuid4()),
        pr_number=pr_number,
        principle_id=principle_id,
        severity=severity,
        details=details,
        timestamp=_now_utc(now),
    )

    ledger = _load_drift_ledger(store)
    ledger.append(_drift_to_dict(event))
    _save_drift_ledger(store, ledger)

    return event


# ---------------------------------------------------------------------------
# grant_variance
# ---------------------------------------------------------------------------


def grant_variance(
    store: Any,
    tag: VarianceTag,
    *,
    pr_number: int,
    config: GuardianConfig,
    affected_paths: list[str] | None = None,
    now: datetime | None = None,
) -> DebtTimer:
    """Create a debt timer for a declared variance and persist it.

    Parameters
    ----------
    store:
        A ``MemoryStore`` instance.
    tag:
        The parsed :class:`VarianceTag` from the PR body.
    pr_number:
        The PR that declared the variance.
    config:
        Guardian configuration; ``variance_default_days`` is used as fallback
        when ``tag.expires_in_days`` is 0 or negative.
    affected_paths:
        Optional list of file paths affected by this variance.
    now:
        Timestamp override for testing.

    Returns
    -------
    DebtTimer
        The newly created timer (already persisted).
    """
    created = _now_utc(now)
    days = tag.expires_in_days if tag.expires_in_days > 0 else config.variance_default_days
    expires = created + timedelta(days=days)

    timer = DebtTimer(
        id=str(uuid.uuid4()),
        pr_number=pr_number,
        principle_id=tag.principle_id,
        justification=tag.justification,
        created_at=created,
        expires_at=expires,
        resolved_at=None,
        level=DebtLevel.NEUTRAL,
        affected_paths=affected_paths or [],
    )

    timers = _load_debt_timers(store)
    timers.append(_timer_to_dict(timer))
    _save_debt_timers(store, timers)

    return timer


# ---------------------------------------------------------------------------
# check_debt_timers
# ---------------------------------------------------------------------------


def check_debt_timers(
    store: Any,
    *,
    now: datetime | None = None,
) -> dict[str, list[DebtTimer]]:
    """Classify all unresolved debt timers by escalation status.

    Escalation levels (spec §5):
    - ``active``            — less than 75 % of lifespan elapsed
    - ``approaching_expiry`` — 75 % or more elapsed but not yet expired
    - ``expired``           — past ``expires_at``

    Resolved timers are excluded from all buckets.

    Parameters
    ----------
    store:
        A ``MemoryStore`` instance.
    now:
        Timestamp override for testing.

    Returns
    -------
    dict with keys ``"active"``, ``"approaching_expiry"``, ``"expired"``,
    each mapping to a list of :class:`DebtTimer`.
    """
    reference = _now_utc(now)
    raw_timers = _load_debt_timers(store)

    active: list[DebtTimer] = []
    approaching: list[DebtTimer] = []
    expired: list[DebtTimer] = []

    for d in raw_timers:
        timer = _timer_from_dict(d)

        if timer.resolved_at is not None:
            # Already resolved — skip
            continue

        created = _ensure_aware(timer.created_at)
        expires = _ensure_aware(timer.expires_at)
        lifespan = (expires - created).total_seconds()

        if reference >= expires:
            expired.append(timer)
        elif lifespan > 0:
            elapsed_fraction = (reference - created).total_seconds() / lifespan
            if elapsed_fraction >= _REMINDER_75_FRACTION:
                approaching.append(timer)
            else:
                active.append(timer)
        else:
            # Zero-duration timer created and immediately expired
            expired.append(timer)

    return {
        "active": active,
        "approaching_expiry": approaching,
        "expired": expired,
    }


# ---------------------------------------------------------------------------
# escalate_debt
# ---------------------------------------------------------------------------


def escalate_debt(
    store: Any,
    debt_id: str,
    *,
    new_level: DebtLevel,
    config: GuardianConfig,
) -> DebtTimer:
    """Bump a debt timer to *new_level* and persist the change.

    The ``BLOCKING`` level is always recorded.  The returned ``blocks_pr``
    attribute on the timer is not part of the :class:`DebtTimer` model; instead
    this function returns a ``(timer, blocks_pr)`` named result.  Callers
    should check :func:`escalate_debt_result` if they need the flag.

    Because :class:`DebtTimer` has no ``blocks_pr`` field, the blocking
    intent is communicated through the return value of
    :func:`escalate_debt_result`.  This function returns only the updated
    :class:`DebtTimer`.

    Parameters
    ----------
    store:
        A ``MemoryStore`` instance.
    debt_id:
        The id of the :class:`DebtTimer` to escalate.
    new_level:
        Target :class:`DebtLevel`.
    config:
        Guardian configuration; ``enable_blocking_escalation`` gates the
        blocking side effect.

    Returns
    -------
    DebtTimer
        The updated timer (already persisted).

    Raises
    ------
    KeyError
        If no timer with *debt_id* exists.
    """
    timers = _load_debt_timers(store)
    for i, d in enumerate(timers):
        if d["id"] == debt_id:
            timer = _timer_from_dict(d)
            timer = timer.model_copy(update={"level": new_level})
            timers[i] = _timer_to_dict(timer)
            _save_debt_timers(store, timers)
            return timer

    raise KeyError(f"No debt timer with id '{debt_id}'")


def escalate_debt_result(
    store: Any,
    debt_id: str,
    *,
    new_level: DebtLevel,
    config: GuardianConfig,
) -> tuple[DebtTimer, bool]:
    """Like :func:`escalate_debt` but also returns ``blocks_pr``.

    Returns
    -------
    tuple[DebtTimer, bool]
        The updated timer and a boolean indicating whether the merge should be
        blocked.  ``blocks_pr`` is ``True`` only when *new_level* is
        ``BLOCKING`` **and** ``config.enable_blocking_escalation`` is ``True``.
    """
    timer = escalate_debt(store, debt_id, new_level=new_level, config=config)
    blocks_pr = new_level == DebtLevel.BLOCKING and config.enable_blocking_escalation
    return timer, blocks_pr


# ---------------------------------------------------------------------------
# resolve_debt
# ---------------------------------------------------------------------------


def resolve_debt(
    store: Any,
    debt_id: str,
    *,
    resolved_pr: int | None = None,
    now: datetime | None = None,
) -> DebtTimer:
    """Mark a debt timer as resolved and persist the change.

    Parameters
    ----------
    store:
        A ``MemoryStore`` instance.
    debt_id:
        The id of the :class:`DebtTimer` to resolve.
    resolved_pr:
        Optional PR number that resolved the debt (recorded in ``details`` via
        the ``resolved_at`` timestamp; the model has no dedicated field for it,
        so callers may store it in ``justification`` if needed).
    now:
        Timestamp override for testing.

    Returns
    -------
    DebtTimer
        The updated timer (already persisted).

    Raises
    ------
    KeyError
        If no timer with *debt_id* exists.
    """
    resolved_at = _now_utc(now)
    timers = _load_debt_timers(store)

    for i, d in enumerate(timers):
        if d["id"] == debt_id:
            timer = _timer_from_dict(d)
            justification = timer.justification
            if resolved_pr is not None:
                justification = f"{justification} [resolved by PR #{resolved_pr}]"
            timer = timer.model_copy(
                update={
                    "resolved_at": resolved_at,
                    "justification": justification,
                }
            )
            timers[i] = _timer_to_dict(timer)
            _save_debt_timers(store, timers)
            return timer

    raise KeyError(f"No debt timer with id '{debt_id}'")
