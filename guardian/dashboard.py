"""Visualization module for NorthStarGuardian.

Implements the five Visualization Tools from spec §7.4:
  - render_dashboard   — orchestrate all charts into a self-contained HTML
  - generate_gantt     — Mermaid Gantt (saga timelines)
  - generate_gitgraph  — Mermaid GitGraph (branching + drift markers)
  - generate_quadrant  — Mermaid QuadrantChart (strategic value vs tech debt)
  - generate_mindmap   — Mermaid Mindmap (changes → North Star principles)

All generate_* functions return raw Mermaid source strings with no HTML
wrapping. render_dashboard embeds them into dashboard.html.j2 and writes the
result to ``memory/dashboard.html`` via store.write().
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from guardian.chronicle import read_chronicle
from guardian.memory import MemoryStore
from guardian.models import (
    DriftEvent,
    InterviewReport,
    JournalEntry,
    NorthStar,
    Saga,
    SagaStatus,
    Verdict,
)

_TEMPLATES_DIR = Path(__file__).parent / "templates"


def _jinja_env() -> Environment:
    return Environment(
        loader=FileSystemLoader(str(_TEMPLATES_DIR)),
        autoescape=False,
        keep_trailing_newline=True,
    )


# ---------------------------------------------------------------------------
# Mermaid helpers
# ---------------------------------------------------------------------------


def _mermaid_safe(text: str) -> str:
    """Strip characters that break Mermaid label parsing."""
    # Replace colon (used as separator in many directives) and quotes
    text = text.replace(":", " -").replace('"', "'")
    return text.strip()


def _today_str() -> str:
    return datetime.now(tz=UTC).strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# generate_gantt
# ---------------------------------------------------------------------------


def generate_gantt(sagas: list[Saga]) -> str:
    """Return a Mermaid Gantt chart source string for *sagas*.

    Each saga becomes a section; each PR in that saga becomes a bar.
    If a saga has no ``end_date``, today is used as the end.
    Sagas with no PRs still appear as a placeholder task.
    """
    today = _today_str()
    lines: list[str] = [
        "gantt",
        "    title Saga Timeline",
        "    dateFormat YYYY-MM-DD",
        "    axisFormat %b %d",
    ]

    if not sagas:
        # Mermaid gantt requires at least one task; emit a placeholder.
        lines += [
            "    section No Sagas",
            "    No data : 2000-01-01, 1d",
        ]
        return "\n".join(lines)

    for saga in sagas:
        section_name = _mermaid_safe(saga.name)
        lines.append(f"    section {section_name}")

        start = saga.start_date.strftime("%Y-%m-%d")
        end = saga.end_date.strftime("%Y-%m-%d") if saga.end_date else today

        status_modifier = ""
        if saga.status == SagaStatus.COMPLETED:
            status_modifier = "done, "
        elif saga.status == SagaStatus.ABANDONED:
            status_modifier = "crit, "

        if saga.pr_numbers:
            for pr in saga.pr_numbers:
                task_label = f"PR #{pr}"
                lines.append(f"    {task_label} : {status_modifier}{start}, {end}")
        else:
            lines.append(f"    {section_name} : {status_modifier}{start}, {end}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# generate_gitgraph
# ---------------------------------------------------------------------------


def generate_gitgraph(
    drift_events: list[DriftEvent],
    journal: list[JournalEntry],
) -> str:
    """Return a Mermaid GitGraph source string.

    Strategy (simplified topology, not real git history):
      - ``main`` branch holds one commit per journal entry in order.
      - Drift events appear as commits tagged with their principle id on a
        ``drift`` branch that branches off main and is merged back.
      - The gitgraph is deliberately simple; we do not attempt to recover
        real branch topology.
    """
    drift_prs: set[int] = {de.pr_number for de in drift_events}

    lines: list[str] = [
        "gitGraph",
        '   commit id: "init"',
    ]

    if not journal:
        return "\n".join(lines)

    # Sort entries chronologically
    sorted_entries = sorted(journal, key=lambda e: e.timestamp)

    drift_branch_open = False

    for entry in sorted_entries:
        label = _mermaid_safe(f"PR #{entry.pr_number}")
        is_drift = entry.pr_number in drift_prs

        if is_drift and not drift_branch_open:
            lines.append("   branch drift")
            drift_branch_open = True

        if is_drift:
            # Find matching drift events for annotation
            matching = [de for de in drift_events if de.pr_number == entry.pr_number]
            tag_text = ""
            if matching:
                principle = _mermaid_safe(matching[0].principle_id)
                tag_text = f' tag: "{principle}"'
            lines.append(f'   commit id: "{label}"{tag_text}')
        else:
            if drift_branch_open:
                lines.append("   checkout main")
                lines.append("   merge drift")
                drift_branch_open = False
            lines.append(f'   commit id: "{label}"')

    # Close any open drift branch
    if drift_branch_open:
        lines.append("   checkout main")
        lines.append("   merge drift")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# generate_quadrant
# ---------------------------------------------------------------------------


def generate_quadrant(
    journal: list[JournalEntry],
    reports: list[InterviewReport] | None = None,
) -> str:
    """Return a Mermaid QuadrantChart source string.

    Axes:
      x-axis: Technical Debt (low → high)
      y-axis: Strategic Value (low → high)

    Score derivation from verdict:
      aligned   → high value (0.75), low debt (0.25)
      ambiguous → middle    (0.50), middle   (0.50)
      drift     → low value (0.25), high debt (0.75)

    Points are jittered slightly by PR number mod to avoid perfect overlap.
    """
    lines: list[str] = [
        "quadrantChart",
        "    title Strategic Value vs Technical Debt",
        "    x-axis Low Technical Debt --> High Technical Debt",
        "    y-axis Low Strategic Value --> High Strategic Value",
        "    quadrant-1 High Value / Low Debt",
        "    quadrant-2 High Value / High Debt",
        "    quadrant-3 Low Value / High Debt",
        "    quadrant-4 Low Value / Low Debt",
    ]

    if not journal:
        return "\n".join(lines)

    _VERDICT_SCORES: dict[Verdict, tuple[float, float]] = {
        Verdict.ALIGNED: (0.25, 0.75),  # (debt, value)
        Verdict.AMBIGUOUS: (0.50, 0.50),
        Verdict.DRIFT: (0.75, 0.25),
    }

    for entry in journal:
        debt_base, value_base = _VERDICT_SCORES[entry.verdict]
        # Small jitter from pr_number so overlapping verdicts spread out
        jitter = (entry.pr_number % 7) * 0.03 - 0.09
        debt = round(max(0.05, min(0.95, debt_base + jitter)), 2)
        value = round(max(0.05, min(0.95, value_base - jitter)), 2)
        label = f"PR#{entry.pr_number}"
        lines.append(f"    {label}: [{debt}, {value}]")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# generate_mindmap
# ---------------------------------------------------------------------------


def generate_mindmap(
    north_star: NorthStar,
    recent_journal: list[JournalEntry],
) -> str:
    """Return a Mermaid Mindmap source string.

    Root: project name.
    Level 1 branches: each North Star principle.
    Level 2 leaves: recent PRs attached to the principle they most touched.

    PRs are attached to a principle when a PrincipleEvaluation with
    ``relevant=True`` can be found in the journal entry's body (we do a
    best-effort parse of the stored body_markdown table rows).
    Unattached PRs go to an "Unassigned" branch.
    """
    root = _mermaid_safe(north_star.project_name)
    lines: list[str] = [
        "mindmap",
        f"  root(({root}))",
    ]

    # Map principle_id → list of PR labels
    principle_prs: dict[str, list[str]] = {p.id: [] for p in north_star.principles}
    unassigned: list[str] = []

    for entry in recent_journal:
        pr_label = f"PR #{entry.pr_number}"
        # Try to extract a principle_id from the markdown table stored in body
        assigned = False
        for principle in north_star.principles:
            pattern = re.compile(
                r"\|\s*`?" + re.escape(principle.id) + r"`?\s*\|",
                re.IGNORECASE,
            )
            if pattern.search(entry.body_markdown):
                principle_prs[principle.id].append(pr_label)
                assigned = True
                break
        if not assigned:
            unassigned.append(pr_label)

    # Emit principle branches
    for principle in sorted(north_star.principles, key=lambda p: p.rank):
        p_label = _mermaid_safe(principle.text[:50])  # truncate long principle text
        lines.append(f"    {p_label}")
        for pr_label in principle_prs[principle.id]:
            lines.append(f"      {pr_label}")

    if unassigned:
        lines.append("    Unassigned")
        for pr_label in unassigned:
            lines.append(f"      {pr_label}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# render_dashboard
# ---------------------------------------------------------------------------


def render_dashboard(store: MemoryStore, north_star: NorthStar) -> str:
    """Orchestrate all chart generators and render the dashboard HTML.

    Reads all chronicle data from *store*, generates each Mermaid chart,
    embeds everything into ``dashboard.html.j2``, writes the result to
    ``memory/dashboard.html``, and returns the HTML string.
    """
    journal = read_chronicle(store)
    drift_events = _load_drift_events(store)
    sagas = _load_all_sagas(store)

    gantt_src = generate_gantt(sagas)
    gitgraph_src = generate_gitgraph(drift_events, journal)
    quadrant_src = generate_quadrant(journal)
    mindmap_src = generate_mindmap(north_star, journal)

    generated_at = datetime.now(tz=UTC).strftime("%Y-%m-%d %H:%M UTC")

    env = _jinja_env()
    template = env.get_template("dashboard.html.j2")
    html = template.render(
        project_name=north_star.project_name,
        identity_statement=north_star.identity_statement,
        principles=north_star.principles,
        gantt_src=gantt_src,
        gitgraph_src=gitgraph_src,
        quadrant_src=quadrant_src,
        mindmap_src=mindmap_src,
        generated_at=generated_at,
    )

    store.write("memory/dashboard.html", html)
    return html


# ---------------------------------------------------------------------------
# Private loaders
# ---------------------------------------------------------------------------


def _load_all_sagas(store: MemoryStore) -> list[Saga]:
    """Load all sagas from the saga index."""
    from guardian.chronicle import (  # local import to avoid circular
        _load_saga_index,
        _saga_from_index_entry,
    )

    index = _load_saga_index(store)
    sagas: list[Saga] = []
    for entry in index.get("sagas", []):
        try:
            sagas.append(_saga_from_index_entry(entry))
        except (KeyError, ValueError):
            continue
    return sagas


def _load_drift_events(store: MemoryStore) -> list[DriftEvent]:
    """Load drift events from memory/drift-ledger.json, returning [] if absent."""
    if not store.exists("memory/drift-ledger.json"):
        return []
    try:
        raw = store.read_json("memory/drift-ledger.json")
        events: list[DriftEvent] = []
        for item in raw.get("events", []):
            events.append(DriftEvent(**item))
        return events
    except (ValueError, KeyError):
        return []
