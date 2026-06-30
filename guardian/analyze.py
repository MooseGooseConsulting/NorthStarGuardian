"""Analysis tools for NorthStarGuardian.

Implements:
    analyze_diff        — pure Python, no LLM; parses unified diff via unidiff
    evaluate_alignment  — LLM; per-principle verdict against the North Star
    detect_anti_patterns — LLM; matches declared anti-patterns to diff locations
    assess_intent       — LLM; infers PR goal, parses [VARIANCE: ...] tags
    run_interview       — orchestrates the above into a full InterviewReport
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import unidiff
from jinja2 import Environment, FileSystemLoader

from guardian.models import (
    AntiPatternMatch,
    DiffAnalysis,
    FileChange,
    IntentSummary,
    InterviewReport,
    NorthStar,
    PrincipleEvaluation,
    VarianceTag,
    Verdict,
)

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class LLMOutputError(ValueError):
    """Raised when the LLM returns output that cannot be parsed into the
    expected schema.  Callers should treat this as a transient failure and
    may retry or surface the raw response for debugging.
    """


# ---------------------------------------------------------------------------
# Jinja2 environment
# ---------------------------------------------------------------------------

_TEMPLATE_DIR = Path(__file__).parent / "templates" / "prompts"
_jinja_env = Environment(
    loader=FileSystemLoader(str(_TEMPLATE_DIR)),
    autoescape=False,
    keep_trailing_newline=True,
)


def _render(template_name: str, **ctx: Any) -> str:
    return _jinja_env.get_template(template_name).render(**ctx)


# ---------------------------------------------------------------------------
# Import-detection regexes  (language-agnostic best-effort)
# ---------------------------------------------------------------------------

# Python: "import X" or "from X import Y" on an added line
_PY_IMPORT_RE = re.compile(r"^(?:import\s+\S+|from\s+\S+\s+import\s+\S+)")

# JS/TS: import ... from '...'  or  require('...')
_JS_IMPORT_RE = re.compile(
    r"""^(?:import\s.*?from\s+['"]|(?:const|let|var)\s+.*?=\s*require\s*\()"""
)

# Dependency-file names that signal new package additions
_DEP_FILES = {
    "pyproject.toml",
    "setup.cfg",
    "setup.py",
    "package.json",
    "package-lock.json",
    "yarn.lock",
    "Pipfile",
    "Pipfile.lock",
}
_REQUIREMENTS_RE = re.compile(r"requirements.*\.txt$", re.IGNORECASE)

# [VARIANCE: <principle_id> — <justification>, will resolve within <N> days]
# Tolerant regex: captures everything after the colon up to the closing bracket.
_VARIANCE_RAW_RE = re.compile(
    r"\[VARIANCE:\s*(?P<body>[^\]]+)\]",
    re.IGNORECASE,
)
# Within the body: "principleId — justification ... <N> days"
_VARIANCE_DAYS_RE = re.compile(r"(\d+)\s*days?", re.IGNORECASE)
# Principle id is the first word-token up to whitespace or em-dash
_VARIANCE_PRINC_RE = re.compile(r"^([A-Za-z0-9_\-\.]+)")


# ---------------------------------------------------------------------------
# analyze_diff — pure Python, no LLM
# ---------------------------------------------------------------------------


def analyze_diff(unified_diff: str, pr_meta: dict[str, Any]) -> DiffAnalysis:
    """Parse *unified_diff* and extract structural information.

    Parameters
    ----------
    unified_diff:
        A unified-format diff string (e.g. from ``git diff``).
    pr_meta:
        A dict with at minimum: ``number`` (int), ``title`` (str),
        ``author`` (str), ``base_sha`` (str), ``head_sha`` (str).
        Optional keys: ``body`` (str), ``commit_messages`` (list[str]).

    Returns
    -------
    DiffAnalysis
        Populated with per-file stats, new imports, and new_packages if any
        dependency file was touched.
    """
    patch = unidiff.PatchSet.from_string(unified_diff)

    file_changes: list[FileChange] = []
    new_packages: list[str] = []

    for pf in patch:
        path = pf.path

        # Determine status
        if pf.is_added_file:
            status = "added"
        elif pf.is_removed_file:
            status = "removed"
        elif pf.is_rename:
            status = "renamed"
        else:
            status = "modified"

        # Collect added/removed lines for import detection
        added_lines: list[str] = []
        removed_lines: list[str] = []

        for hunk in pf:
            for diff_line in hunk:
                raw = str(diff_line)
                # unidiff prefixes lines with +/- in the string representation
                if diff_line.line_type == "+":
                    content = raw[1:].strip()
                    added_lines.append(content)
                elif diff_line.line_type == "-":
                    content = raw[1:].strip()
                    removed_lines.append(content)

        new_imports = _extract_imports(added_lines)
        removed_imports = _extract_imports(removed_lines)

        file_changes.append(
            FileChange(
                path=path,
                status=status,
                additions=pf.added,
                deletions=pf.removed,
                new_imports=new_imports,
                removed_imports=removed_imports,
            )
        )

        # Flag dependency-file touches
        basename = Path(path).name
        is_dep_file = basename in _DEP_FILES or bool(_REQUIREMENTS_RE.search(basename))
        if is_dep_file:
            # Extract added dependency names from added lines (best-effort)
            for line in added_lines:
                name = _extract_dep_name(line)
                if name:
                    new_packages.append(name)

    total_additions = sum(f.additions for f in file_changes)
    total_deletions = sum(f.deletions for f in file_changes)

    return DiffAnalysis(
        pr_number=int(pr_meta.get("number", 0)),
        pr_title=str(pr_meta.get("title", "")),
        pr_body=pr_meta.get("body"),
        author=str(pr_meta.get("author", "")),
        base_sha=str(pr_meta.get("base_sha", "")),
        head_sha=str(pr_meta.get("head_sha", "")),
        files=file_changes,
        new_packages=list(dict.fromkeys(new_packages)),  # deduplicate, preserve order
        summary_stats={
            "total_additions": total_additions,
            "total_deletions": total_deletions,
            "files_changed": len(file_changes),
        },
    )


def _extract_imports(lines: list[str]) -> list[str]:
    """Return import module references found in *lines*."""
    results: list[str] = []
    for line in lines:
        if _PY_IMPORT_RE.match(line) or _JS_IMPORT_RE.match(line):
            results.append(line)
    return results


def _extract_dep_name(line: str) -> str | None:
    """Best-effort: extract a package name from a dependency-file added line."""
    line = line.strip()
    if not line or line.startswith("#") or line.startswith("["):
        return None
    # requirements.txt style: "requests>=2.0" → "requests"
    name = re.split(r"[><=!~\s;#]", line)[0].strip().strip('"').strip("'")
    # pyproject style value lines like 'anthropic>=0.40.0', or '"anthropic"'
    if name and re.match(r"^[A-Za-z0-9_\-\.]+$", name):
        return name
    return None


# ---------------------------------------------------------------------------
# LLM helper
# ---------------------------------------------------------------------------


def _call_llm(
    client: Any,
    *,
    model: str,
    system: str,
    user: str,
    max_tokens: int = 4096,
) -> str:
    """Call client.chat.completions.create and return the first text content."""
    messages: list[dict[str, str]] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": user})
    response = client.chat.completions.create(
        model=model,
        max_tokens=max_tokens,
        messages=messages,
    )
    if not response.choices or response.choices[0].message.content is None:
        raise LLMOutputError("LLM returned empty response")
    content = response.choices[0].message.content
    if content:
        return content
    raise LLMOutputError("LLM response contained no text content")


def _parse_json_response(raw: str, context: str) -> Any:
    """Strip optional markdown fences and parse JSON from *raw*.

    Raises LLMOutputError with *context* if parsing fails.
    """
    text = raw.strip()
    # Strip ```json ... ``` or ``` ... ``` fences if present
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise LLMOutputError(
            f"JSON parse failure in {context}: {exc}\nRaw output:\n{raw[:500]}"
        ) from exc


# ---------------------------------------------------------------------------
# evaluate_alignment
# ---------------------------------------------------------------------------


def evaluate_alignment(
    diff: DiffAnalysis,
    north_star: NorthStar,
    *,
    client: Any,
    model: str,
) -> list[PrincipleEvaluation]:
    """Evaluate the diff against each principle in the North Star.

    Makes a single LLM call. Irrelevant principles are returned with
    ``relevant=False`` and no verdict so the report stays brief.

    Parameters
    ----------
    diff:
        Structural analysis produced by :func:`analyze_diff`.
    north_star:
        The project's North Star document.
    client:
        An ``openai.OpenAI`` (or compatible) client instance.
    model:
        Model identifier string.

    Returns
    -------
    list[PrincipleEvaluation]
        One entry per principle, ordered by principle rank.

    Raises
    ------
    LLMOutputError
        If the LLM response cannot be parsed or does not match the schema.
    """
    prompt = _render(
        "evaluate_alignment.md.j2",
        north_star=north_star,
        diff=diff,
    )

    raw = _call_llm(
        client,
        model=model,
        system=(
            "You are the Repository Guardian. Follow the instructions exactly. "
            "Return only valid JSON with no preamble."
        ),
        user=prompt,
    )

    data = _parse_json_response(raw, "evaluate_alignment")
    if not isinstance(data, list):
        raise LLMOutputError(f"evaluate_alignment expected a JSON array, got {type(data).__name__}")

    evaluations: list[PrincipleEvaluation] = []
    principle_ids = {p.id for p in north_star.principles}

    for item in data:
        if not isinstance(item, dict):
            raise LLMOutputError(
                f"evaluate_alignment: expected dict items, got {type(item).__name__}"
            )
        pid = item.get("principle_id", "")
        if pid not in principle_ids:
            # LLM invented a principle id — skip rather than crash
            continue
        relevant = bool(item.get("relevant", False))
        verdict_raw = item.get("verdict")
        verdict: Verdict | None = None
        if relevant and verdict_raw:
            try:
                verdict = Verdict(verdict_raw)
            except ValueError as exc:
                raise LLMOutputError(
                    f"evaluate_alignment: invalid verdict '{verdict_raw}' for principle '{pid}'"
                ) from exc

        evaluations.append(
            PrincipleEvaluation(
                principle_id=pid,
                relevant=relevant,
                verdict=verdict,
                reasoning=item.get("reasoning") if relevant else None,
                citations=item.get("citations", []) if relevant else [],
            )
        )

    # Ensure every principle has an entry (guard against LLM omissions)
    returned_ids = {e.principle_id for e in evaluations}
    for principle in sorted(north_star.principles, key=lambda p: p.rank):
        if principle.id not in returned_ids:
            evaluations.append(
                PrincipleEvaluation(
                    principle_id=principle.id,
                    relevant=False,
                    verdict=None,
                    reasoning=None,
                    citations=[],
                )
            )

    # Sort by North Star rank order
    rank_map = {p.id: p.rank for p in north_star.principles}
    evaluations.sort(key=lambda e: rank_map.get(e.principle_id, 999))

    return evaluations


# ---------------------------------------------------------------------------
# detect_anti_patterns
# ---------------------------------------------------------------------------


def detect_anti_patterns(
    diff: DiffAnalysis,
    north_star: NorthStar,
    *,
    client: Any,
    model: str,
) -> list[AntiPatternMatch]:
    """Check the diff against the North Star's anti-patterns registry.

    Makes a single LLM call.  Returns an empty list if no anti-patterns are
    registered or none match.

    Raises
    ------
    LLMOutputError
        If the LLM response cannot be parsed.
    """
    if not north_star.anti_patterns:
        return []

    prompt = _render(
        "detect_anti_patterns.md.j2",
        north_star=north_star,
        diff=diff,
    )

    raw = _call_llm(
        client,
        model=model,
        system=(
            "You are the Repository Guardian. Follow the instructions exactly. "
            "Return only valid JSON with no preamble."
        ),
        user=prompt,
    )

    data = _parse_json_response(raw, "detect_anti_patterns")
    if not isinstance(data, list):
        raise LLMOutputError(
            f"detect_anti_patterns expected a JSON array, got {type(data).__name__}"
        )

    pattern_ids = {ap.id for ap in north_star.anti_patterns}
    matches: list[AntiPatternMatch] = []

    for item in data:
        if not isinstance(item, dict):
            raise LLMOutputError(
                f"detect_anti_patterns: expected dict items, got {type(item).__name__}"
            )
        pid = item.get("pattern_id", "")
        if pid not in pattern_ids:
            continue
        location = str(item.get("location", "unknown"))
        explanation = str(item.get("explanation", ""))
        matches.append(
            AntiPatternMatch(
                pattern_id=pid,
                location=location,
                explanation=explanation,
            )
        )

    return matches


# ---------------------------------------------------------------------------
# assess_intent  (also parses [VARIANCE: ...] tags — pure Python, no LLM)
# ---------------------------------------------------------------------------


def assess_intent(
    pr_meta: dict[str, Any],
    diff: DiffAnalysis,
    *,
    client: Any,
    model: str,
) -> IntentSummary:
    """Infer the developer's goal from PR metadata via a single LLM call.

    Also parses ``[VARIANCE: ...]`` tags from *pr_meta['body']* using regex
    (no LLM required for that step).

    Parameters
    ----------
    pr_meta:
        Raw PR metadata dict.  Recognised keys: ``title``, ``body``,
        ``commit_messages`` (list[str]), ``author``, ``number``.
    diff:
        DiffAnalysis for context injected into the prompt.

    Returns
    -------
    IntentSummary
        ``declared_variances`` is populated from the PR body; ``one_line`` and
        ``paragraph`` come from the LLM.

    Raises
    ------
    LLMOutputError
        If the LLM response cannot be parsed.
    """
    # Parse variance tags first (pure Python)
    body_text = pr_meta.get("body") or ""
    declared_variances = _parse_variance_tags(body_text)

    prompt = _render(
        "assess_intent.md.j2",
        north_star=_DUMMY_NORTH_STAR_FOR_INTENT,  # placeholder; real one passed by run_interview
        pr_meta=pr_meta,
        diff=diff,
    )

    raw = _call_llm(
        client,
        model=model,
        system=(
            "You are the Repository Guardian. Follow the instructions exactly. "
            "Return only valid JSON with no preamble."
        ),
        user=prompt,
        max_tokens=1024,
    )

    data = _parse_json_response(raw, "assess_intent")
    if not isinstance(data, dict):
        raise LLMOutputError(f"assess_intent expected a JSON object, got {type(data).__name__}")

    one_line = str(data.get("one_line", ""))
    paragraph = str(data.get("paragraph", ""))

    if not one_line:
        raise LLMOutputError("assess_intent: LLM returned empty 'one_line' field")

    return IntentSummary(
        one_line=one_line,
        paragraph=paragraph,
        declared_variances=declared_variances,
    )


def assess_intent_with_north_star(
    pr_meta: dict[str, Any],
    diff: DiffAnalysis,
    north_star: NorthStar,
    *,
    client: Any,
    model: str,
) -> IntentSummary:
    """Variant of :func:`assess_intent` that injects the real North Star into
    the prompt.  Called by :func:`run_interview`; public callers may use either.
    """
    body_text = pr_meta.get("body") or ""
    declared_variances = _parse_variance_tags(body_text)

    prompt = _render(
        "assess_intent.md.j2",
        north_star=north_star,
        pr_meta=pr_meta,
        diff=diff,
    )

    raw = _call_llm(
        client,
        model=model,
        system=(
            "You are the Repository Guardian. Follow the instructions exactly. "
            "Return only valid JSON with no preamble."
        ),
        user=prompt,
        max_tokens=1024,
    )

    data = _parse_json_response(raw, "assess_intent")
    if not isinstance(data, dict):
        raise LLMOutputError(f"assess_intent expected a JSON object, got {type(data).__name__}")

    one_line = str(data.get("one_line", ""))
    paragraph = str(data.get("paragraph", ""))

    if not one_line:
        raise LLMOutputError("assess_intent: LLM returned empty 'one_line' field")

    return IntentSummary(
        one_line=one_line,
        paragraph=paragraph,
        declared_variances=declared_variances,
    )


# Minimal stub North Star used when assess_intent is called standalone
# (without a real north_star).  The prompt still renders cleanly.
_DUMMY_NORTH_STAR_FOR_INTENT = type(
    "_DummyNorthStar",
    (),
    {
        "project_name": "",
        "identity_statement": "",
        "principles": [],
        "approved_architecture": "",
        "anti_patterns": [],
    },
)()


def _parse_variance_tags(text: str) -> list[VarianceTag]:
    """Extract ``[VARIANCE: ...]`` declarations from PR body text.

    Parsing rules (best-effort, not LLM):
    - Principle id: first whitespace/dash-delimited token after the colon.
    - expires_in_days: first integer followed by "day" or "days"; default 7.
    - justification: everything after the principle id, stripped of leading
      punctuation and the "days" clause.
    """
    tags: list[VarianceTag] = []
    for m in _VARIANCE_RAW_RE.finditer(text):
        raw_full = m.group(0)
        body = m.group("body").strip()

        # Extract principle id
        princ_match = _VARIANCE_PRINC_RE.match(body)
        principle_id = princ_match.group(1) if princ_match else "unknown"

        # Extract expiry days
        days_match = _VARIANCE_DAYS_RE.search(body)
        expires_in_days = int(days_match.group(1)) if days_match else 7

        # Build justification: strip principle id from front, strip days clause
        justification = body
        if princ_match:
            justification = re.sub(r"^[\s\u2014\u2013:-]+", "", body[princ_match.end() :]).strip()
        if days_match:
            # Remove the "N days" fragment from the justification
            justification = justification[: days_match.start()].rstrip(" ,;").strip()
            if not justification:
                justification = body  # fallback to full body

        tags.append(
            VarianceTag(
                principle_id=principle_id,
                justification=justification,
                expires_in_days=expires_in_days,
                raw=raw_full,
            )
        )
    return tags


# ---------------------------------------------------------------------------
# run_interview — top-level orchestration
# ---------------------------------------------------------------------------


def run_interview(
    diff: DiffAnalysis,
    north_star: NorthStar,
    *,
    client: Any,
    model: str,
    pr_meta: dict[str, Any] | None = None,
) -> InterviewReport:
    """Compose a full :class:`InterviewReport` for a single PR.

    Orchestration order:
    1. assess_intent (PR title/body/commits → IntentSummary)
    2. evaluate_alignment (diff + North Star → per-principle verdicts)
    3. detect_anti_patterns (diff + anti-patterns registry → matches)
    4. One more LLM call to draft the chronicle paragraph.

    Overall verdict: worst per-principle outcome (drift > ambiguous > aligned).

    Parameters
    ----------
    diff:
        Structural analysis produced by :func:`analyze_diff`.
    north_star:
        The project's North Star.
    client:
        OpenAI client instance.
    model:
        Model identifier string.
    pr_meta:
        Raw PR metadata dict (merged with diff fields as fallback).

    Returns
    -------
    InterviewReport
    """
    if pr_meta is None:
        pr_meta = {}

    # Merge diff fields as fallback so prompts always have something
    _merged_meta: dict[str, Any] = {
        "number": diff.pr_number,
        "title": diff.pr_title,
        "body": diff.pr_body,
        "author": diff.author,
        **pr_meta,
    }

    # Step 1 — intent
    intent = assess_intent_with_north_star(
        _merged_meta,
        diff,
        north_star,
        client=client,
        model=model,
    )

    # Step 2 — alignment
    evaluations = evaluate_alignment(diff, north_star, client=client, model=model)

    # Step 3 — anti-patterns
    ap_matches = detect_anti_patterns(diff, north_star, client=client, model=model)

    # Step 4 — overall verdict
    overall_verdict = _aggregate_verdict(evaluations)

    # Step 5 — chronicle paragraph via one more LLM call
    chronicle_paragraph = _draft_chronicle(
        diff=diff,
        north_star=north_star,
        intent=intent,
        evaluations=evaluations,
        overall_verdict=overall_verdict,
        client=client,
        model=model,
    )

    return InterviewReport(
        pr_number=diff.pr_number,
        overall_verdict=overall_verdict,
        alignment_summary=intent.one_line,
        principle_evaluations=evaluations,
        anti_pattern_matches=ap_matches,
        saga_id=None,  # saga assignment is handled by the chronicle module
        suggestions=[],  # concrete suggestions are a downstream concern
        chronicle_paragraph=chronicle_paragraph,
        intent=intent,
        created_at=datetime.now(tz=UTC),
    )


def _aggregate_verdict(evaluations: list[PrincipleEvaluation]) -> Verdict:
    """Return the worst verdict among relevant evaluations.

    Priority: drift > ambiguous > aligned.  If no principles are relevant,
    default to ``aligned``.
    """
    verdicts = {e.verdict for e in evaluations if e.relevant and e.verdict is not None}
    if Verdict.DRIFT in verdicts:
        return Verdict.DRIFT
    if Verdict.AMBIGUOUS in verdicts:
        return Verdict.AMBIGUOUS
    return Verdict.ALIGNED


def _draft_chronicle(
    *,
    diff: DiffAnalysis,
    north_star: NorthStar,
    intent: IntentSummary,
    evaluations: list[PrincipleEvaluation],
    overall_verdict: Verdict,
    client: Any,
    model: str,
) -> str:
    """Ask the LLM to write a single chronicle paragraph for the PR."""
    relevant = [e for e in evaluations if e.relevant]
    eval_summary = "\n".join(
        f"- [{e.principle_id}] {e.verdict}: {e.reasoning or ''}" for e in relevant
    )

    system = (
        "You are the Repository Guardian. Write a single journal-entry paragraph "
        "in past tense. Be factual, brief (3–5 sentences), historically grounded. "
        "Do not praise or moralize. No JSON — return plain prose only."
    )
    user = (
        f"Project: {north_star.project_name}\n"
        f"PR #{diff.pr_number}: {diff.pr_title}\n"
        f"Author: {diff.author}\n"
        f"Intent: {intent.paragraph}\n"
        f"Overall verdict: {overall_verdict.value}\n"
        f"Relevant principle evaluations:\n{eval_summary or '(none)'}\n\n"
        "Write the chronicle paragraph for this PR."
    )

    return _call_llm(client, model=model, system=system, user=user, max_tokens=512)
