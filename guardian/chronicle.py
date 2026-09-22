"""Chronicle module for NorthStarGuardian.

Implements the four Chronicle Tools from spec §7.3:
  - write_journal_entry  — compose and persist a narrative journal entry
  - assign_saga          — LLM-assisted saga assignment or creation
  - update_saga          — append PR to saga, persist
  - read_chronicle       — filtered retrieval of journal entries
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader

from guardian.analyze import LLMOutputError
from guardian.memory import MemoryStore
from guardian.models import (
    IntentSummary,
    InterviewReport,
    JournalEntry,
    PrincipleEvaluation,
    Saga,
    SagaStatus,
    Verdict,
)

_SAGA_INDEX_PATH = "memory/sagas/_index.json"

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_TEMPLATES_DIR = Path(__file__).parent / "templates" / "prompts"


def _jinja_env() -> Environment:
    env = Environment(
        loader=FileSystemLoader(str(_TEMPLATES_DIR)),
        autoescape=False,
        keep_trailing_newline=True,
    )

    def _format_date(value: datetime) -> str:
        return value.strftime("%Y-%m-%d")

    env.filters["format_date"] = _format_date
    return env


def _slug(name: str) -> str:
    """Convert a saga name to a URL/filename-safe slug."""
    slug = name.lower()
    slug = re.sub(r"[^\w\s-]", "", slug)
    slug = re.sub(r"[\s_]+", "-", slug)
    slug = re.sub(r"-+", "-", slug).strip("-")
    return slug or "unnamed-saga"


def _ensure_unique_slug(base_slug: str, existing_ids: set[str]) -> str:
    """Append a numeric suffix if *base_slug* clashes with an existing id."""
    candidate = base_slug
    counter = 2
    while candidate in existing_ids:
        candidate = f"{base_slug}-{counter}"
        counter += 1
    return candidate


def _verdict_symbol(verdict: Verdict) -> str:
    return {
        Verdict.ALIGNED: "aligned",
        Verdict.AMBIGUOUS: "ambiguous",
        Verdict.DRIFT: "drift",
    }[verdict]


def _compose_journal_markdown(
    report: InterviewReport,
    saga: Saga | None,
) -> str:
    """Return a complete Markdown document for a single journal entry."""
    ts = report.created_at.strftime("%Y-%m-%d %H:%M:%S UTC")
    saga_id = saga.id if saga else "unassigned"
    saga_name = saga.name if saga else "—"

    # YAML frontmatter
    lines: list[str] = [
        "---",
        f"pr_number: {report.pr_number}",
        f"saga_id: {saga_id}",
        f"verdict: {report.overall_verdict.value}",
        f"timestamp: {ts}",
        "---",
        "",
        f"# PR #{report.pr_number} — {_verdict_symbol(report.overall_verdict).upper()}",
        "",
    ]

    # Chronicle paragraph (past-tense narrative, from the report)
    lines.append(report.chronicle_paragraph)
    lines.append("")

    # Alignment summary
    lines.append(f"**Alignment Summary:** {report.alignment_summary}")
    lines.append("")

    # Principle evaluations table (relevant ones only)
    relevant: list[PrincipleEvaluation] = [pe for pe in report.principle_evaluations if pe.relevant]
    if relevant:
        lines.append("## Principle Evaluations")
        lines.append("")
        lines.append("| Principle | Verdict | Reasoning |")
        lines.append("|-----------|---------|-----------|")
        for pe in relevant:
            verdict_str = pe.verdict.value if pe.verdict else "—"
            reasoning = (pe.reasoning or "").replace("|", "\\|").replace("\n", " ")
            lines.append(f"| `{pe.principle_id}` | {verdict_str} | {reasoning} |")
        lines.append("")

    # Saga assignment
    lines.append(f"**Saga:** {saga_name} (`{saga_id}`)")
    lines.append("")

    # Suggestions
    if report.suggestions:
        lines.append("## Suggestions")
        lines.append("")
        for s in report.suggestions:
            lines.append(f"- {s}")
        lines.append("")

    return "\n".join(lines)


def _load_saga_index(store: MemoryStore) -> dict[str, Any]:
    """Return the saga index dict, creating an empty one if absent."""
    if store.exists(_SAGA_INDEX_PATH):
        return store.read_json(_SAGA_INDEX_PATH)
    return {"sagas": []}


def _save_saga_index(store: MemoryStore, index: dict[str, Any]) -> None:
    store.write_json(_SAGA_INDEX_PATH, index)


def _saga_from_index_entry(entry: dict[str, Any]) -> Saga:
    """Reconstruct a Saga from a stored index entry or saga-file frontmatter dict."""
    return Saga(
        id=entry["id"],
        name=entry["name"],
        start_date=datetime.fromisoformat(entry["start_date"]),
        end_date=datetime.fromisoformat(entry["end_date"]) if entry.get("end_date") else None,
        status=SagaStatus(entry.get("status", "active")),
        pr_numbers=entry.get("pr_numbers", []),
        description=entry.get("description", ""),
    )


def _saga_to_frontmatter(saga: Saga) -> str:
    """Serialise a Saga to YAML frontmatter + description body."""
    lines = [
        "---",
        f"id: {saga.id}",
        f"name: {saga.name}",
        f"start_date: {saga.start_date.isoformat()}",
    ]
    if saga.end_date:
        lines.append(f"end_date: {saga.end_date.isoformat()}")
    else:
        lines.append("end_date: null")
    lines.append(f"status: {saga.status.value}")
    pr_list = json.dumps(saga.pr_numbers)
    lines.append(f"pr_numbers: {pr_list}")
    lines += [
        "---",
        "",
        saga.description,
    ]
    return "\n".join(lines)


def _load_saga_from_store(store: MemoryStore, saga_id: str) -> Saga:
    """Read and parse a saga file from the store."""
    raw = store.read(f"memory/sagas/{saga_id}.md")
    # Parse YAML frontmatter between --- delimiters
    parts = raw.split("---", 2)
    if len(parts) < 3:
        raise ValueError(f"Malformed saga file for id={saga_id}")
    fm_text = parts[1]
    body = parts[2].strip()

    fm: dict[str, Any] = {}
    for line in fm_text.strip().splitlines():
        if ":" in line:
            key, _, val = line.partition(":")
            fm[key.strip()] = val.strip()

    # pr_numbers is stored as a JSON array string
    pr_raw = fm.get("pr_numbers", "[]")
    try:
        pr_numbers = json.loads(pr_raw)
    except json.JSONDecodeError:
        pr_numbers = []

    return Saga(
        id=fm["id"],
        name=fm["name"],
        start_date=datetime.fromisoformat(fm["start_date"]),
        end_date=datetime.fromisoformat(fm["end_date"])
        if fm.get("end_date") not in (None, "null", "")
        else None,
        status=SagaStatus(fm.get("status", "active")),
        pr_numbers=pr_numbers,
        description=body,
    )


def _load_journal_entry(store: MemoryStore, path: str) -> JournalEntry | None:
    """Parse a journal file and return a JournalEntry, or None on parse failure."""
    try:
        raw = store.read(path)
    except FileNotFoundError:
        return None

    parts = raw.split("---", 2)
    if len(parts) < 3:
        return None

    fm: dict[str, Any] = {}
    for line in parts[1].strip().splitlines():
        if ":" in line:
            key, _, val = line.partition(":")
            fm[key.strip()] = val.strip()

    try:
        return JournalEntry(
            pr_number=int(fm["pr_number"]),
            timestamp=datetime.fromisoformat(fm["timestamp"].replace(" UTC", "+00:00")),
            saga_id=fm.get("saga_id") or None,
            verdict=Verdict(fm["verdict"]),
            body_markdown=parts[2].strip(),
        )
    except (KeyError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def write_journal_entry(
    store: MemoryStore,
    report: InterviewReport,
    saga: Saga | None,
) -> JournalEntry:
    """Compose and persist a narrative journal entry for *report*.

    The file is written to ``memory/journal/YYYY-MM-DD-pr-NN.md``.
    Returns the created :class:`JournalEntry`.
    """
    date_str = report.created_at.strftime("%Y-%m-%d")
    filename = f"memory/journal/{date_str}-pr-{report.pr_number}.md"
    body = _compose_journal_markdown(report, saga)
    store.write(filename, body)

    return JournalEntry(
        pr_number=report.pr_number,
        timestamp=report.created_at,
        saga_id=saga.id if saga else None,
        verdict=report.overall_verdict,
        body_markdown=body,
    )


def assign_saga(
    store: MemoryStore,
    intent: IntentSummary,
    existing_sagas: list[Saga],
    *,
    client: Any,
    model: str,
) -> Saga:
    """Use the OpenAI API to assign *intent* to an existing saga or create a new one.

    Returns the matched or newly created :class:`Saga`. New sagas are persisted
    immediately (file + index update). Matched sagas are returned as-is; the
    caller should call :func:`update_saga` to append the PR.
    """
    active_sagas = [s for s in existing_sagas if s.status == SagaStatus.ACTIVE]

    env = _jinja_env()
    template = env.get_template("assign_saga.md.j2")
    prompt = template.render(intent=intent, active_sagas=active_sagas)

    message = client.chat.completions.create(
        model=model,
        max_tokens=256,
        messages=[{"role": "user", "content": prompt}],
    )
    if not message.choices or message.choices[0].message.content is None:
        raise LLMOutputError("LLM returned empty response")
    response_text = message.choices[0].message.content.strip()

    # Parse LLM response
    if response_text.startswith("MATCH:"):
        saga_id = response_text.removeprefix("MATCH:").strip()
        # Find the saga in existing list
        for s in active_sagas:
            if s.id == saga_id:
                return s
        # Fallback: try loading from store directly
        try:
            return _load_saga_from_store(store, saga_id)
        except (FileNotFoundError, ValueError):
            pass
        # If we couldn't find it, fall through to create a new saga

    # CREATE path (or fallback from failed MATCH)
    new_name = "New Saga"
    if response_text.startswith("CREATE:"):
        new_name = response_text.removeprefix("CREATE:").strip()

    # Build unique slug
    existing_ids = {s.id for s in existing_sagas}
    base_slug = _slug(new_name)
    unique_slug = _ensure_unique_slug(base_slug, existing_ids)

    new_saga = Saga(
        id=unique_slug,
        name=new_name,
        start_date=datetime.now(UTC),
        status=SagaStatus.ACTIVE,
        pr_numbers=[],
        description="",
    )

    # Persist the new saga file
    store.write(f"memory/sagas/{unique_slug}.md", _saga_to_frontmatter(new_saga))

    # Update index
    index = _load_saga_index(store)
    index["sagas"].append(
        {
            "id": new_saga.id,
            "name": new_saga.name,
            "start_date": new_saga.start_date.isoformat(),
            "end_date": None,
            "status": new_saga.status.value,
            "pr_numbers": new_saga.pr_numbers,
            "description": new_saga.description,
        }
    )
    _save_saga_index(store, index)

    return new_saga


def update_saga(store: MemoryStore, saga: Saga, pr_number: int) -> Saga:
    """Append *pr_number* to *saga* and persist the updated saga file.

    Returns the updated :class:`Saga`. If *pr_number* is already in
    ``saga.pr_numbers`` this is a no-op (idempotent).
    """
    if pr_number in saga.pr_numbers:
        return saga

    updated = Saga(
        id=saga.id,
        name=saga.name,
        start_date=saga.start_date,
        end_date=saga.end_date,
        status=saga.status,
        pr_numbers=[*saga.pr_numbers, pr_number],
        description=saga.description,
    )

    # Persist saga file
    store.write(f"memory/sagas/{updated.id}.md", _saga_to_frontmatter(updated))

    # Update index entry
    index = _load_saga_index(store)
    for entry in index["sagas"]:
        if entry["id"] == updated.id:
            entry["pr_numbers"] = updated.pr_numbers
            break
    else:
        # Not in index yet — add it
        index["sagas"].append(
            {
                "id": updated.id,
                "name": updated.name,
                "start_date": updated.start_date.isoformat(),
                "end_date": updated.end_date.isoformat() if updated.end_date else None,
                "status": updated.status.value,
                "pr_numbers": updated.pr_numbers,
                "description": updated.description,
            }
        )
    _save_saga_index(store, index)

    return updated


def read_chronicle(
    store: MemoryStore,
    *,
    since: datetime | None = None,
    saga_id: str | None = None,
    drift_only: bool = False,
) -> list[JournalEntry]:
    """Return journal entries matching the given filters.

    Parameters
    ----------
    since:
        If provided, only return entries with ``timestamp >= since``.
    saga_id:
        If provided, only return entries assigned to this saga.
    drift_only:
        If True, only return entries with ``verdict == Verdict.DRIFT``.
    """
    paths = store.list("memory/journal")
    entries: list[JournalEntry] = []

    for path in sorted(paths):
        if not path.endswith(".md"):
            continue
        entry = _load_journal_entry(store, path)
        if entry is None:
            continue

        # Apply filters
        if since is not None and entry.timestamp < since:
            continue
        if saga_id is not None and entry.saga_id != saga_id:
            continue
        if drift_only and entry.verdict != Verdict.DRIFT:
            continue

        entries.append(entry)

    return entries
