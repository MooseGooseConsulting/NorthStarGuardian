"""North Star tools for NorthStarGuardian.

Provides read/write/amend operations for Guardian's active copy of the
project North Star, stored under ``.github/guardian``.
"""

from __future__ import annotations

import json
import re
import subprocess
import textwrap
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from jinja2 import Environment, PackageLoader

from guardian.memory import MemoryStore
from guardian.models import Amendment, AntiPattern, NorthStar, Principle

# Paths inside .github/guardian.
_NORTH_STAR_PATH = "northstar.md"
_AMENDMENT_LOG_PATH = "memory/amendment-log.md"
_REPO_NORTH_STAR_PATH = "docs/northstar.md"


# ---------------------------------------------------------------------------
# Markdown round-trip
# ---------------------------------------------------------------------------

_FRONTMATTER_RE = re.compile(r"^---\r?\n(.*?)\r?\n---\r?\n", re.DOTALL)


def parse_north_star_markdown(text: str) -> NorthStar:
    """Parse a North Star from its Markdown+JSON-frontmatter representation."""
    m = _FRONTMATTER_RE.match(text)
    if not m:
        raise ValueError("northstar.md is missing YAML frontmatter")

    raw: dict[str, Any] = json.loads(m.group(1)) or {}

    return NorthStar(
        version=raw.get("version", 1),
        project_name=raw.get("project_name", ""),
        identity_statement=raw.get("identity_statement", ""),
        principles=_parse_principles(raw),
        approved_architecture=raw.get("approved_architecture", ""),
        anti_patterns=_parse_anti_patterns(raw),
        created_at=_parse_datetime(raw.get("created_at")) or datetime.now(tz=UTC),
        amended_at=_parse_datetime(raw.get("amended_at")),
    )


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, str):
        return datetime.fromisoformat(value)
    if isinstance(value, datetime):
        return value
    return None


def _parse_principles(raw: dict[str, Any]) -> list[Principle]:
    return [
        Principle(
            id=p["id"],
            rank=p["rank"],
            text=p["text"],
            rationale=p.get("rationale"),
            tags=p.get("tags", []),
        )
        for p in raw.get("principles", [])
    ]


def _parse_anti_patterns(raw: dict[str, Any]) -> list[AntiPattern]:
    return [
        AntiPattern(
            id=a["id"],
            description=a["description"],
            example=a.get("example"),
            detect=a.get("detect"),
        )
        for a in raw.get("anti_patterns", [])
    ]


def render_north_star_markdown(c: NorthStar) -> str:
    """Render a North Star to the canonical Markdown+YAML-frontmatter format.

    The output is fully round-trippable via :func:`parse_north_star_markdown`.
    """
    yaml_block = json.dumps(
        _frontmatter_from_north_star(c),
        indent=2,
        ensure_ascii=False,
        default=str,
    )
    return f"---\n{yaml_block}\n---\n{_body_from_north_star(c)}\n"


def _frontmatter_from_north_star(c: NorthStar) -> dict[str, Any]:
    frontmatter: dict[str, Any] = {
        "version": c.version,
        "project_name": c.project_name,
        "identity_statement": c.identity_statement,
        "approved_architecture": c.approved_architecture,
        "created_at": c.created_at.isoformat(),
        "principles": [_principle_to_frontmatter(p) for p in c.principles],
        "anti_patterns": [_anti_pattern_to_frontmatter(a) for a in c.anti_patterns],
    }
    if c.amended_at:
        frontmatter["amended_at"] = c.amended_at.isoformat()
    return frontmatter


def _principle_to_frontmatter(p: Principle) -> dict[str, Any]:
    item: dict[str, Any] = {"id": p.id, "rank": p.rank, "text": p.text}
    if p.rationale:
        item["rationale"] = p.rationale
    if p.tags:
        item["tags"] = p.tags
    return item


def _anti_pattern_to_frontmatter(a: AntiPattern) -> dict[str, Any]:
    item: dict[str, Any] = {"id": a.id, "description": a.description}
    if a.example:
        item["example"] = a.example
    if a.detect:
        item["detect"] = a.detect
    return item


def _body_from_north_star(c: NorthStar) -> str:
    body_lines = [
        f"# {c.project_name} — North Star\n",
        "## Identity\n",
        c.identity_statement,
        "\n## Principles\n",
    ]

    body_lines.extend(_principle_body_lines(c.principles))
    body_lines.append("\n## Approved Architecture\n")
    body_lines.append(c.approved_architecture)
    body_lines.extend(_anti_pattern_body_lines(c.anti_patterns))
    return "\n".join(body_lines)


def _principle_body_lines(principles: list[Principle]) -> list[str]:
    lines: list[str] = []
    for p in sorted(principles, key=lambda x: x.rank):
        lines.append(f"### {p.rank}. {p.text}\n")
        if p.rationale:
            lines.append(f"{p.rationale}\n")
    return lines


def _anti_pattern_body_lines(anti_patterns: list[AntiPattern]) -> list[str]:
    if not anti_patterns:
        return []

    lines = ["\n\n## Anti-Patterns\n"]
    for a in anti_patterns:
        lines.append(f"### {a.id}\n")
        lines.append(f"{a.description}\n")
        if a.example:
            lines.append(f"*Example:* {a.example}\n")
        if a.detect:
            lines.append(f"*Detect:* `{a.detect}`\n")
    return lines


# ---------------------------------------------------------------------------
# Core I/O
# ---------------------------------------------------------------------------


def read_north_star(store: MemoryStore) -> NorthStar:
    """Read and parse Guardian's active North Star copy."""
    text = store.read(_NORTH_STAR_PATH)
    return parse_north_star_markdown(text)


def write_north_star(
    store: MemoryStore,
    c: NorthStar,
    rationale: str = "initial",
) -> None:
    """Serialise and write *c* to Guardian's active copy."""
    store.write(_NORTH_STAR_PATH, render_north_star_markdown(c), message=rationale)


# ---------------------------------------------------------------------------
# Repo source-of-truth I/O
# ---------------------------------------------------------------------------


def _resolve_repo_path(repo_root: Path, path: str) -> Path:
    relative = Path(path)
    if relative.is_absolute():
        raise ValueError("Repo North Star path must be relative")
    root = repo_root.resolve()
    full = (root / relative).resolve()
    if full != root and root not in full.parents:
        raise ValueError(f"Path '{path}' escapes outside repository")
    return full


def read_repo_north_star_markdown(
    repo_root: Path,
    path: str = _REPO_NORTH_STAR_PATH,
    *,
    ref: str | None = None,
) -> str:
    """Read the repo-authored North Star Markdown.

    When *ref* is provided, the file is read from that git object instead of
    the current working tree.  PR review uses this to evaluate against the base
    branch policy rather than a PR's modified policy.
    """
    if ref:
        result = subprocess.run(
            ["git", "show", f"{ref}:{path}"],
            cwd=str(repo_root),
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise FileNotFoundError(
                f"{path} was not found at git ref {ref}: {result.stderr.strip()}"
            )
        return result.stdout

    full = _resolve_repo_path(repo_root, path)
    if not full.exists():
        raise FileNotFoundError(f"{path} does not exist")
    return full.read_text(encoding="utf-8")


def read_repo_north_star(
    repo_root: Path,
    path: str = _REPO_NORTH_STAR_PATH,
    *,
    ref: str | None = None,
) -> NorthStar:
    """Read and parse the repo-authored North Star."""
    return parse_north_star_markdown(read_repo_north_star_markdown(repo_root, path=path, ref=ref))


def write_repo_north_star(
    repo_root: Path,
    c: NorthStar,
    path: str = _REPO_NORTH_STAR_PATH,
) -> None:
    """Write the human-facing repo North Star to ``docs/northstar.md``."""
    full = _resolve_repo_path(repo_root, path)
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(render_north_star_markdown(c), encoding="utf-8")


# ---------------------------------------------------------------------------
# Amendment
# ---------------------------------------------------------------------------


def amend_north_star(
    store: MemoryStore,
    target: str,
    target_id: str | None,
    after: str,
    rationale: str,
    actor: str,
) -> NorthStar:
    """Apply an amendment to the North Star and record it in the log.

    *target* must be one of ``"identity"``, ``"principle"``,
    ``"anti_pattern"``, or ``"architecture"``.

    Returns the updated North Star (staged but not committed).
    """
    c = read_north_star(store)
    now = datetime.now(tz=UTC)
    c, before = _apply_amendment(c, target, target_id, after, now)

    c = c.model_copy(update={"version": c.version + 1})
    amendment = Amendment(
        timestamp=now,
        actor=actor,
        target=target,  # type: ignore[arg-type]
        target_id=target_id,
        before=before,
        after=after,
        rationale=rationale,
    )

    write_north_star(store, c, rationale=rationale)
    append_amendment(store, amendment)
    return c


def _apply_amendment(
    c: NorthStar,
    target: str,
    target_id: str | None,
    after: str,
    now: datetime,
) -> tuple[NorthStar, str | None]:
    if target == "identity":
        return c.model_copy(update={"identity_statement": after, "amended_at": now}), (
            c.identity_statement
        )
    if target == "architecture":
        return c.model_copy(update={"approved_architecture": after, "amended_at": now}), (
            c.approved_architecture
        )
    if target == "principle":
        return _amend_principle(c, target_id, after, now)
    if target == "anti_pattern":
        return _amend_anti_pattern(c, target_id, after, now)
    raise ValueError(
        f"Unknown amendment target '{target}'. "
        "Expected: identity, principle, anti_pattern, architecture"
    )


def _amend_principle(
    c: NorthStar,
    target_id: str | None,
    after: str,
    now: datetime,
) -> tuple[NorthStar, str]:
    if target_id is None:
        raise ValueError("target_id is required when target='principle'")
    principles = list(c.principles)
    for i, p in enumerate(principles):
        if p.id == target_id:
            principles[i] = p.model_copy(update={"text": after})
            return c.model_copy(update={"principles": principles, "amended_at": now}), p.text
    raise ValueError(f"Principle '{target_id}' not found in North Star")


def _amend_anti_pattern(
    c: NorthStar,
    target_id: str | None,
    after: str,
    now: datetime,
) -> tuple[NorthStar, str]:
    if target_id is None:
        raise ValueError("target_id is required when target='anti_pattern'")
    patterns = list(c.anti_patterns)
    for i, a in enumerate(patterns):
        if a.id == target_id:
            patterns[i] = a.model_copy(update={"description": after})
            return c.model_copy(update={"anti_patterns": patterns, "amended_at": now}), (
                a.description
            )
    raise ValueError(f"Anti-pattern '{target_id}' not found in North Star")


def append_amendment(store: MemoryStore, amendment: Amendment) -> None:
    """Append *amendment* to the amendment log (stages; does not commit)."""
    existing = ""
    if store.exists(_AMENDMENT_LOG_PATH):
        existing = store.read(_AMENDMENT_LOG_PATH)

    entry = textwrap.dedent(f"""\
        ## {amendment.timestamp.strftime("%Y-%m-%d %H:%M UTC")} — {amendment.target}

        - **Actor:** {amendment.actor}
        - **Target:** {amendment.target}{f" / {amendment.target_id}" if amendment.target_id else ""}
        - **Rationale:** {amendment.rationale}

        **Before:**
        {amendment.before or "*(none)*"}

        **After:**
        {amendment.after}

        ---
    """)

    if not existing:
        header = "# Amendment Log\n\nAll North Star changes, in chronological order.\n\n"
        content = header + entry
    else:
        content = existing + "\n" + entry

    store.write(_AMENDMENT_LOG_PATH, content)


# ---------------------------------------------------------------------------
# Initialize
# ---------------------------------------------------------------------------


def initialize_north_star(answers: dict[str, Any], actor: str) -> NorthStar:
    """Turn a dict of setup-flow answers into a North Star.

    The interactive prompts live in ``cli.py``; this function is pure
    data transformation.  Expected *answers* keys:

    - ``project_name`` (str)
    - ``identity_statement`` (str)
    - ``principles`` (list of str, 5–7 items)
    - ``approved_architecture`` (str)
    - ``anti_patterns`` (list of dict with ``description`` and optional
      ``example``, ``detect`` keys; or list of str for simple descriptions)

    Returns an un-persisted NorthStar object; call :func:`write_north_star`
    to save it.
    """
    _ = actor
    now = datetime.now(tz=UTC)

    raw_principles: list[str | dict[str, Any]] = answers.get("principles", [])
    principles: list[Principle] = []
    for i, item in enumerate(raw_principles, start=1):
        if isinstance(item, str):
            principles.append(Principle(id=f"p{i}", rank=i, text=item))
        else:
            principles.append(
                Principle(
                    id=item.get("id", f"p{i}"),
                    rank=item.get("rank", i),
                    text=item["text"],
                    rationale=item.get("rationale"),
                    tags=item.get("tags", []),
                )
            )

    raw_patterns: list[str | dict[str, Any]] = answers.get("anti_patterns", [])
    anti_patterns: list[AntiPattern] = []
    for i, item in enumerate(raw_patterns, start=1):
        if isinstance(item, str):
            anti_patterns.append(AntiPattern(id=f"ap{i}", description=item))
        else:
            anti_patterns.append(
                AntiPattern(
                    id=item.get("id", f"ap{i}"),
                    description=item["description"],
                    example=item.get("example"),
                    detect=item.get("detect"),
                )
            )

    return NorthStar(
        version=1,
        project_name=answers["project_name"],
        identity_statement=answers["identity_statement"],
        principles=principles,
        approved_architecture=answers.get("approved_architecture", ""),
        anti_patterns=anti_patterns,
        created_at=now,
    )


# ---------------------------------------------------------------------------
# Jinja2 template rendering (used by CLI)
# ---------------------------------------------------------------------------


def render_north_star_template(answers: dict[str, Any]) -> str:
    """Render the Jinja2 skeleton template with escaped answer text."""
    env = Environment(
        loader=PackageLoader("guardian", "templates"),
        autoescape=True,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    template = env.get_template("northstar.md.j2")
    return template.render(**answers)
