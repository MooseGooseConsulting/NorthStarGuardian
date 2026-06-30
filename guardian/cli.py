"""CLI entry point for NorthStarGuardian.

Each subcommand corresponds to one stage of the Guardian lifecycle described
in docs/SPEC.md §8–11.  The module is intentionally thin: all domain logic
lives in the imported modules; this file is only orchestration and I/O.
"""

from __future__ import annotations

import hashlib
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import click
from openai import OpenAI

from guardian.github_io import GitHubContext, get_pr_diff, get_pr_meta, post_pr_comment
from guardian.memory import MemoryStore
from guardian.models import GuardianConfig
from guardian.north_star import (
    amend_north_star,
    initialize_north_star,
    parse_north_star_markdown,
    read_north_star,
    read_repo_north_star_markdown,
    write_north_star,
    write_repo_north_star,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_CONFIG_PATH = "guardian-config.json"


def _load_config(store: MemoryStore) -> GuardianConfig:
    """Read GuardianConfig from .github/guardian, defaulting if absent."""
    if store.exists(_CONFIG_PATH):
        try:
            raw = store.read_json(_CONFIG_PATH)
            return GuardianConfig(**raw)
        except Exception:
            pass
    return GuardianConfig()


def _load_review_north_star(
    root: Path,
    store: MemoryStore,
    config: GuardianConfig,
    pr_meta: dict[str, Any],
    *,
    env: dict[str, str] | None = None,
) -> Any:
    """Load the North Star snapshot for a review run and persist active copy."""
    environ = env if env is not None else os.environ
    _GUARDIAN_PREFIX = ".github/guardian/"
    _active = config.north_star.active_copy_path or ".github/guardian/northstar.md"
    northstar_path = (
        _active[len(_GUARDIAN_PREFIX) :]
        if _active.startswith(_GUARDIAN_PREFIX)
        else Path(_active).name
    )

    if config.north_star.source == "linear":
        from guardian.linear import LinearClient

        if not config.linear.document_id:
            raise click.ClickException(
                "Guardian is configured for Linear North Star source but "
                "linear.document_id is not set."
            )
        api_key = environ.get("LINEAR_API_KEY")
        if not api_key:
            raise click.ClickException("LINEAR_API_KEY environment variable is not set.")

        snapshot = LinearClient(api_key).fetch_document(config.linear.document_id)
        store.write(northstar_path, snapshot.content)
        store.write_json(
            "memory/northstar-snapshot.json",
            snapshot.model_dump(mode="json"),
        )
        return parse_north_star_markdown(snapshot.content)

    ref = pr_meta.get("base_sha") or None
    content = read_repo_north_star_markdown(
        root,
        path=config.north_star.repo_path,
        ref=ref,
    )
    store.write(northstar_path, content)
    store.write_json(
        "memory/northstar-snapshot.json",
        {
            "source": "repo",
            "path": config.north_star.repo_path,
            "ref": ref,
            "fetched_at": datetime.now(tz=UTC).isoformat(),
            "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        },
    )
    return parse_north_star_markdown(content)


def _create_linear_amendment_issue(
    config: GuardianConfig,
    *,
    target_id: str,
    new_text: str,
    actor: str,
    env: dict[str, str] | None = None,
) -> Any:
    """Create a Linear issue for a requested North Star amendment."""
    from guardian.linear import LinearClient

    environ = env if env is not None else os.environ
    api_key = environ.get("LINEAR_API_KEY")
    if not api_key:
        raise click.ClickException("LINEAR_API_KEY environment variable is not set.")
    if not config.linear.team_id:
        raise click.ClickException(
            "Linear-backed amendments require linear.team_id in Guardian config."
        )

    description = (
        f"Requested by @{actor} via Guardian `/amend`.\n\n"
        f"Target principle: `{target_id}`\n\n"
        "Proposed text:\n\n"
        f"{new_text}\n\n"
        "Review and apply this in the canonical Linear North Star document."
    )
    return LinearClient(api_key).create_issue(
        team_id=config.linear.team_id,
        project_id=config.linear.project_id,
        title=f"Review North Star amendment for {target_id}",
        description=description,
    )


def _make_openai_client() -> OpenAI:
    """Construct an OpenAI client from the environment."""
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise click.ClickException("OPENAI_API_KEY environment variable is not set.")
    return OpenAI(api_key=api_key)


def _make_store(repo_root: Path | None = None) -> MemoryStore:
    """Create a MemoryStore rooted at *repo_root* (default: cwd)."""
    root = repo_root or Path(os.environ.get("GITHUB_WORKSPACE", ".")).resolve()
    return MemoryStore(root)


def parse_slash_command(body: str) -> tuple[str, list[str]] | None:
    """Parse a slash command from a comment body.

    Returns ``(command, args_list)`` when the body starts with ``/<cmd>``,
    or ``None`` if it doesn't match.

    Examples
    --------
    >>> parse_slash_command("/amend principle-3 new text")
    ('amend', ['principle-3', 'new text'])
    >>> parse_slash_command("not a command")
    None
    """
    body = body.strip()
    m = re.match(r"^/([a-zA-Z][\w-]*)(.*)$", body, re.DOTALL)
    if not m:
        return None

    cmd = m.group(1)
    rest = m.group(2).strip()

    # Split remainder into at most 2 tokens: the first word-token and the
    # rest (for cases like "/amend principle-3 "new text with spaces"").
    if rest:
        # Un-quote a quoted second argument if present.
        quoted = re.match(r'^(\S+)\s+"(.*)"$', rest, re.DOTALL)
        if quoted:
            args = [quoted.group(1), quoted.group(2)]
        else:
            parts = rest.split(None, 1)
            args = parts
    else:
        args = []

    return cmd, args


def _format_report_comment(report: Any, config: GuardianConfig) -> str:
    """Render an InterviewReport as a Markdown PR comment."""
    from guardian.models import Verdict

    verdict_emoji = {
        Verdict.ALIGNED: "✅",
        Verdict.AMBIGUOUS: "⚠️",
        Verdict.DRIFT: "🔴",
    }
    emoji = verdict_emoji.get(report.overall_verdict, "🔵")

    lines: list[str] = [
        f"## {emoji} Guardian Interview — PR #{report.pr_number}",
        "",
        f"**{report.alignment_summary}**",
        "",
    ]

    # Principle evaluations — only the relevant ones.
    relevant = [pe for pe in report.principle_evaluations if pe.relevant]
    if relevant:
        lines.append("### Principle Check")
        for pe in relevant:
            v_emoji = verdict_emoji.get(pe.verdict, "🔵") if pe.verdict else "🔵"
            lines.append(f"- {v_emoji} **{pe.principle_id}**: {pe.reasoning or ''}")
        lines.append("")

    # Anti-pattern matches.
    if report.anti_pattern_matches:
        lines.append("### Anti-Pattern Matches")
        for m in report.anti_pattern_matches:
            lines.append(f"- `{m.location}` — {m.explanation}")
        lines.append("")

    # Saga.
    if report.saga_id:
        lines.append(f"**Saga:** `{report.saga_id}`")
        lines.append("")

    # Suggestions.
    if report.suggestions:
        lines.append("### Suggestions")
        for s in report.suggestions:
            lines.append(f"- {s}")
        lines.append("")

    # Chronicle paragraph.
    lines.append("### Chronicle Entry")
    lines.append(report.chronicle_paragraph)
    lines.append("")

    # Footer.
    if config.pages_url:
        lines.append(f"[View Dashboard]({config.pages_url})")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI root
# ---------------------------------------------------------------------------


@click.group()
def cli() -> None:
    """NorthStarGuardian — advisory PR governance agent."""


# ---------------------------------------------------------------------------
# guardian interview
# ---------------------------------------------------------------------------


@cli.command()
@click.option(
    "--event-path",
    default=None,
    envvar="GITHUB_EVENT_PATH",
    help="Path to the GitHub event JSON payload.",
)
@click.option(
    "--repo-root",
    default=None,
    envvar="GITHUB_WORKSPACE",
    help="Repository root directory.",
)
def interview(event_path: str | None, repo_root: str | None) -> None:
    """Run the full PR interview cycle (triggered by pull_request events)."""
    # Lazy imports of domain modules so tests can mock them before importing cli.
    from guardian import analyze, chronicle, dashboard

    root = Path(repo_root or ".").resolve()
    store = MemoryStore(root)
    store.ensure_initialized()

    config = _load_config(store)
    client = _make_openai_client()

    # Build GitHub context.
    ctx = GitHubContext.from_env(event_path=event_path)
    if ctx.pr is None:
        raise click.ClickException("No pull request found in event payload.")

    click.echo(f"Guardian: interviewing PR #{ctx.pr.number}")

    # 1. Read diff and metadata.
    diff = get_pr_diff(ctx)
    meta = get_pr_meta(ctx)

    # 2. Analyze diff (pre-LLM structural extraction).
    diff_analysis = analyze.analyze_diff(diff, meta)

    # 3. Load the base-branch/Linear North Star and persist Guardian's active copy.
    north_star = _load_review_north_star(root, store, config, meta)

    # 4. Run LLM interview.
    report = analyze.run_interview(
        diff_analysis,
        north_star,
        client=client,
        model=config.openai_model_analysis,
    )

    # 5. Load existing sagas and assign the saga for this PR.
    # _load_saga_index and _saga_from_index_entry are module-internal helpers;
    # accessing them is acceptable here since cli.py is a first-party sibling.
    saga_index = chronicle._load_saga_index(store)
    all_sagas = [chronicle._saga_from_index_entry(entry) for entry in saga_index.get("sagas", [])]
    saga = chronicle.assign_saga(
        store,
        report.intent,
        all_sagas,
        client=client,
        model=config.openai_model_analysis,
    )
    chronicle.update_saga(store, saga, report.pr_number)
    report = report.model_copy(update={"saga_id": saga.id})

    # 6. Write journal entry.
    chronicle.write_journal_entry(store, report, saga)

    # 7. Render dashboard.
    dashboard.render_dashboard(store, north_star)

    # 8. Flush repo-native Guardian state.
    pr_num = ctx.pr.number
    with store.session(f"guardian: interview PR #{pr_num} — {report.overall_verdict.value}"):
        pass  # session commits whatever was staged by the above calls

    # 9. Post report as a PR comment.
    comment_body = _format_report_comment(report, config)
    post_pr_comment(ctx, comment_body)

    click.echo(f"Guardian: interview complete — verdict: {report.overall_verdict.value}")


# ---------------------------------------------------------------------------
# guardian command  (slash-command dispatcher)
# ---------------------------------------------------------------------------


@cli.command(name="command")
@click.option(
    "--event-path",
    default=None,
    envvar="GITHUB_EVENT_PATH",
    help="Path to the GitHub event JSON payload.",
)
@click.option(
    "--repo-root",
    default=None,
    envvar="GITHUB_WORKSPACE",
    help="Repository root directory.",
)
def command_dispatch(event_path: str | None, repo_root: str | None) -> None:
    """Handle issue_comment events containing slash commands."""
    root = Path(repo_root or ".").resolve()
    store = MemoryStore(root)

    ctx = GitHubContext.from_env(event_path=event_path)
    body = ctx.comment_body or ""

    parsed = parse_slash_command(body)
    if parsed is None:
        click.echo("Guardian: comment does not contain a slash command — skipping.")
        return

    cmd, args = parsed
    click.echo(f"Guardian: dispatching /{cmd} with args {args!r}")

    store.ensure_initialized()
    config = _load_config(store)

    handlers: dict[str, Any] = {
        "init-guardian": _handle_init_guardian,
        "re-anchor": _handle_re_anchor,
        "amend": _handle_amend,
        "chronicle": _handle_chronicle,
        "dashboard": _handle_dashboard,
        "status": _handle_status,
    }

    handler = handlers.get(cmd)
    if handler is None:
        reply = (
            f"Guardian: unknown command `/{cmd}`.\n\n"
            "Available commands: `/init-guardian`, `/re-anchor`, `/amend`, "
            "`/chronicle`, `/dashboard`, `/status`."
        )
        if ctx.pr:
            post_pr_comment(ctx, reply)
        else:
            click.echo(reply)
        return

    handler(ctx, store, config, args)


# ---------------------------------------------------------------------------
# Slash-command handlers
# ---------------------------------------------------------------------------


def _handle_init_guardian(
    ctx: GitHubContext,
    store: MemoryStore,
    config: GuardianConfig,
    args: list[str],
) -> None:
    """Handle /init-guardian — post instructions to run init-local."""
    reply = (
        "## Guardian Setup\n\n"
        "To initialize the repo North Star and Guardian active copy, run the "
        "following command locally:\n\n"
        "```\nguardian init-local\n```\n\n"
        "This writes `docs/northstar.md` for humans and "
        "`.github/guardian/northstar.md` for Guardian's active copy. "
        "Configure Linear in `.github/guardian/guardian-config.json` when "
        "Linear should be the canonical policy surface."
    )
    if ctx.pr:
        post_pr_comment(ctx, reply)
    else:
        click.echo(reply)


def _handle_re_anchor(
    ctx: GitHubContext,
    store: MemoryStore,
    config: GuardianConfig,
    args: list[str],
) -> None:
    """Handle /re-anchor — show current North Star summary and instructions."""
    try:
        north_star = read_north_star(store)
        principles_text = "\n".join(f"{p.rank}. {p.text}" for p in north_star.principles)
        reply = (
            f"## Guardian Re-Anchor\n\n"
            f"**Current identity:** {north_star.identity_statement}\n\n"
            f"**Principles:**\n{principles_text}\n\n"
            "Re-anchoring to current North Star sources. "
            "Run /show-northstar to verify the updated anchor."
        )
    except FileNotFoundError:
        reply = (
            "Guardian: no active North Star found. Run `guardian init-local` "
            "or configure `.github/guardian/guardian-config.json` first."
        )

    if ctx.pr:
        post_pr_comment(ctx, reply)
    else:
        click.echo(reply)


def _handle_amend(
    ctx: GitHubContext,
    store: MemoryStore,
    config: GuardianConfig,
    args: list[str],
) -> None:
    """Handle /amend <principle-id> "new text" — amend a specific principle."""
    if len(args) < 2:
        reply = (
            'Usage: `/amend <principle-id> "new text"`\n\n'
            'Example: `/amend p1 "All data flows through the LLM layer"`'
        )
        if ctx.pr:
            post_pr_comment(ctx, reply)
        else:
            click.echo(reply)
        return

    target_id = args[0]
    new_text = args[1]
    actor = ctx.event_payload.get("comment", {}).get("user", {}).get("login", "unknown")

    try:
        if config.north_star.source == "linear":
            issue = _create_linear_amendment_issue(
                config,
                target_id=target_id,
                new_text=new_text,
                actor=actor,
            )
            reply = (
                "## Guardian Amendment Proposed\n\n"
                "Linear-backed North Stars are not edited from PR comments. "
                f"Created `{issue.identifier}` for review: {issue.url}"
            )
        else:
            updated = amend_north_star(
                store,
                target="principle",
                target_id=target_id,
                after=new_text,
                rationale=f"Amended via /amend slash command by @{actor}",
                actor=actor,
            )
            with store.session(f"guardian: amend principle {target_id} via slash command"):
                pass

            reply = (
                f"## Guardian Amendment Applied\n\n"
                f"**Principle `{target_id}`** updated to:\n\n"
                f"> {new_text}\n\n"
                f"North Star version is now **v{updated.version}**."
            )
    except (ValueError, FileNotFoundError, click.ClickException) as exc:
        reply = f"Guardian: amendment failed — {exc}"

    if ctx.pr:
        post_pr_comment(ctx, reply)
    else:
        click.echo(reply)


def _handle_chronicle(
    ctx: GitHubContext,
    store: MemoryStore,
    config: GuardianConfig,
    args: list[str],
) -> None:
    """Handle /chronicle — post recent chronicle entries."""
    from guardian import chronicle

    try:
        entries = chronicle.read_chronicle(store)
        if not entries:
            reply = "Guardian: no journal entries yet."
        else:
            # Post the 5 most recent entries.
            recent = entries[-5:]
            body_parts = ["## Guardian Chronicle (recent entries)\n"]
            for entry in reversed(recent):
                body_parts.append(entry.body_markdown)
                body_parts.append("\n---\n")
            reply = "\n".join(body_parts)
    except Exception as exc:
        reply = f"Guardian: error reading chronicle — {exc}"

    if ctx.pr:
        post_pr_comment(ctx, reply)
    else:
        click.echo(reply)


def _handle_dashboard(
    ctx: GitHubContext,
    store: MemoryStore,
    config: GuardianConfig,
    args: list[str],
) -> None:
    """Handle /dashboard — regenerate the dashboard and post a link."""
    from guardian import dashboard

    try:
        north_star = read_north_star(store)
        dashboard.render_dashboard(store, north_star)
        with store.session("guardian: regenerate dashboard via /dashboard command"):
            pass

        if config.pages_url:
            reply = (
                f"## Guardian Dashboard\n\n"
                f"Dashboard has been regenerated. "
                f"[View it here]({config.pages_url})."
            )
        else:
            reply = (
                "## Guardian Dashboard\n\n"
                "Dashboard has been regenerated at "
                "`.github/guardian/memory/dashboard.html`. Configure `pages_url` "
                "in `.github/guardian/guardian-config.json` for a direct link."
            )
    except Exception as exc:
        reply = f"Guardian: error regenerating dashboard — {exc}"

    if ctx.pr:
        post_pr_comment(ctx, reply)
    else:
        click.echo(reply)


def _handle_status(
    ctx: GitHubContext,
    store: MemoryStore,
    config: GuardianConfig,
    args: list[str],
) -> None:
    """Handle /status — post a brief health summary."""
    from guardian import chronicle, governance

    lines: list[str] = ["## Guardian Status\n"]

    # Active debt timers.
    try:
        buckets = governance.check_debt_timers(store)
        active = buckets.get("active", [])
        approaching = buckets.get("approaching_expiry", [])
        expired = buckets.get("expired", [])
        if expired:
            lines.append(f"**Expired debt timers:** {len(expired)} (need resolution)")
        if approaching:
            lines.append(f"**Approaching expiry:** {len(approaching)}")
        if active:
            lines.append(f"**Active debt timers:** {len(active)}")
        if not active and not approaching and not expired:
            lines.append("**Debt timers:** none active")
    except Exception:
        lines.append("**Debt timers:** unavailable")

    lines.append("")

    # Last interview.
    try:
        entries = chronicle.read_chronicle(store)
        if entries:
            last = entries[-1]
            lines.append(
                f"**Last interview:** PR #{last.pr_number} "
                f"({last.timestamp.strftime('%Y-%m-%d')}) — `{last.verdict.value}`"
            )
        else:
            lines.append("**Last interview:** none recorded")
    except Exception:
        lines.append("**Last interview:** unavailable")

    if ctx.pr:
        post_pr_comment(ctx, "\n".join(lines))
    else:
        click.echo("\n".join(lines))


# ---------------------------------------------------------------------------
# guardian sweep-debt
# ---------------------------------------------------------------------------


@cli.command()
@click.option(
    "--repo-root",
    default=None,
    envvar="GITHUB_WORKSPACE",
    help="Repository root directory.",
)
def sweep_debt(repo_root: str | None) -> None:
    """Scheduled job: check debt timers and escalate expired ones."""
    from guardian import governance

    root = Path(repo_root or ".").resolve()
    store = MemoryStore(root)
    store.ensure_initialized()

    buckets = governance.check_debt_timers(store)
    active = buckets.get("active", [])
    approaching = buckets.get("approaching_expiry", [])
    expired = buckets.get("expired", [])
    click.echo(
        f"Guardian sweep-debt: {len(active)} active, "
        f"{len(approaching)} approaching, {len(expired)} expired timers"
    )

    if not expired and not approaching:
        click.echo("Guardian sweep-debt: nothing to escalate.")
        return

    from guardian.models import DebtLevel

    config = _load_config(store)

    for debt in approaching:
        governance.escalate_debt(store, debt.id, new_level=DebtLevel.REMINDER_75, config=config)
        click.echo(f"  Reminded (75%) debt {debt.id} (PR #{debt.pr_number}, {debt.principle_id})")

    for debt in expired:
        governance.escalate_debt(
            store, debt.id, new_level=DebtLevel.REMINDER_EXPIRED, config=config
        )
        click.echo(f"  Escalated debt {debt.id} (PR #{debt.pr_number}, {debt.principle_id})")

    with store.session("guardian: sweep-debt escalation run"):
        pass

    # Post a tracking issue summarising escalations.
    token = os.environ.get("GITHUB_TOKEN")
    repo_name = os.environ.get("GITHUB_REPOSITORY")
    if token and repo_name:
        from github import Github

        gh = Github(token)
        repo = gh.get_repo(repo_name)

        lines = ["## Guardian Debt Escalation Report\n"]
        for debt in expired:
            lines.append(
                f"- PR #{debt.pr_number} — `{debt.principle_id}` "
                f"(expired {debt.expires_at.strftime('%Y-%m-%d')})"
            )
        body = "\n".join(lines)

        repo.create_issue(
            title=f"Guardian: {len(expired)} expired debt timer(s) — {datetime.now(tz=UTC).strftime('%Y-%m-%d')}",
            body=body,
            labels=["guardian", "tech-debt"],
        )
        click.echo("Guardian sweep-debt: tracking issue created.")


# ---------------------------------------------------------------------------
# guardian init-local
# ---------------------------------------------------------------------------


@cli.command()
@click.option(
    "--repo-root",
    default=".",
    show_default=True,
    help="Repository root directory.",
)
def init_local(repo_root: str) -> None:
    """Interactive setup: walk through North Star initialization locally."""
    root = Path(repo_root).resolve()
    store = MemoryStore(root)

    click.echo("Guardian — North Star Setup")
    click.echo("=" * 40)

    # Project identity.
    project_name = click.prompt("Project name")
    click.echo(
        "\nProject identity statement: one paragraph describing what this project IS "
        "and what it is NOT. (e.g. 'This is an LLM-powered analysis pipeline. "
        "It is not a collection of standalone scripts.')"
    )
    identity_statement = click.prompt("Identity statement")

    # Principles (5-7).
    click.echo(
        "\nPrinciples: enter 5–7 rank-ordered tenets that define the project's soul. "
        "Press Enter with an empty line to stop (minimum 5)."
    )
    raw_principles: list[str] = []
    while len(raw_principles) < 7:
        n = len(raw_principles) + 1
        value = click.prompt(f"Principle {n}", default="", show_default=False)
        if not value:
            if len(raw_principles) >= 5:
                break
            click.echo(f"  Please enter at least {5 - len(raw_principles)} more principle(s).")
        else:
            raw_principles.append(value)

    # Approved architecture paragraph.
    click.echo(
        "\nApproved architecture: one paragraph describing the canonical packages, "
        "patterns, and paradigms. (e.g. 'We use Gin for HTTP, GORM for persistence.')"
    )
    approved_architecture = click.prompt("Approved architecture")

    # Anti-patterns (optional).
    click.echo(
        "\nAnti-patterns: explicit examples of what drift looks like. "
        "Press Enter with an empty line to skip."
    )
    raw_anti_patterns: list[str] = []
    while True:
        n = len(raw_anti_patterns) + 1
        value = click.prompt(
            f"Anti-pattern {n} (or Enter to finish)", default="", show_default=False
        )
        if not value:
            break
        raw_anti_patterns.append(value)

    answers: dict[str, Any] = {
        "project_name": project_name,
        "identity_statement": identity_statement,
        "principles": raw_principles,
        "approved_architecture": approved_architecture,
        "anti_patterns": raw_anti_patterns,
    }

    north_star = initialize_north_star(answers, actor="local-setup")

    click.echo("\nInitializing .github/guardian state...")
    store.ensure_initialized()
    write_repo_north_star(root, north_star)
    write_north_star(store, north_star, rationale="Initial North Star via init-local")
    if not store.exists(_CONFIG_PATH):
        store.write_json(_CONFIG_PATH, GuardianConfig().model_dump(mode="json"))

    with store.session("guardian: initialize North Star via init-local"):
        pass

    click.echo(
        f"\nNorth Star written for '{project_name}' with "
        f"{len(north_star.principles)} principles and "
        f"{len(north_star.anti_patterns)} anti-patterns."
    )
    click.echo("Repo source: docs/northstar.md")
    click.echo("Guardian active copy: .github/guardian/northstar.md")
    click.echo("Guardian is ready.")


# ---------------------------------------------------------------------------
# guardian preview-dashboard
# ---------------------------------------------------------------------------


@cli.command()
@click.option(
    "--output",
    default="dashboard-preview.html",
    show_default=True,
    help="Output HTML file path.",
)
@click.option(
    "--repo-root",
    default=".",
    show_default=True,
    help="Repository root directory.",
)
def preview_dashboard(output: str, repo_root: str) -> None:
    """Local dev: render the dashboard to a local HTML file."""
    from guardian import dashboard

    root = Path(repo_root).resolve()
    store = MemoryStore(root)
    store.ensure_initialized()

    try:
        north_star = read_north_star(store)
    except FileNotFoundError as err:
        raise click.ClickException(
            "No Guardian active North Star found at "
            "`.github/guardian/northstar.md`. Run `guardian init-local` first."
        ) from err

    html = dashboard.render_dashboard(store, north_star)

    out = Path(output)
    out.write_text(html, encoding="utf-8")
    click.echo(f"Dashboard written to: {out.resolve()}")


# ---------------------------------------------------------------------------
# guardian chronograph-* local stewardship pipeline
# ---------------------------------------------------------------------------


@cli.command(name="chronograph-recommend-actions")
@click.option(
    "--repo-root",
    default=".",
    show_default=True,
    help="Repository root directory.",
)
@click.option(
    "--diffs",
    default="diffs.json",
    show_default=True,
    help="Chronograph diff JSON file under .github/guardian/chronograph.",
)
def chronograph_recommend_actions(repo_root: str, diffs: str) -> None:
    """Stage 07: turn curated config diffs into stewardship recommendations."""
    from guardian import chronograph

    root = Path(repo_root).resolve()
    policy = chronograph.ChronographSafetyPolicy.for_repo(root)
    diff_path = chronograph.chronograph_path(root, diffs)
    output_path = chronograph.chronograph_path(root, "recommendations.json")

    actions = chronograph.recommend_actions(chronograph.load_diffs(diff_path))
    chronograph.write_json(output_path, actions)

    click.echo(
        "Chronograph: wrote "
        f"{len(actions)} recommendation(s) to {output_path.relative_to(policy.repo_root)}"
    )


@cli.command(name="chronograph-plan-apply")
@click.option(
    "--repo-root",
    default=".",
    show_default=True,
    help="Repository root directory.",
)
def chronograph_plan_apply(repo_root: str) -> None:
    """Stage 08: create an audited apply plan from recommendations."""
    from guardian import chronograph

    root = Path(repo_root).resolve()
    policy = chronograph.ChronographSafetyPolicy.for_repo(root)
    recommendations_path = chronograph.chronograph_path(root, "recommendations.json")
    plan_path = chronograph.chronograph_path(root, "apply-plan.json")

    actions = chronograph.load_actions(recommendations_path)
    plan = chronograph.build_apply_plan(actions, policy=policy)
    chronograph.write_json(plan_path, plan)

    auto_apply = sum(1 for item in plan if item.auto_apply)
    click.echo(
        "Chronograph: wrote apply plan with "
        f"{len(plan)} item(s), {auto_apply} auto-applicable, "
        f"to {plan_path.relative_to(policy.repo_root)}"
    )


@cli.command(name="chronograph-apply")
@click.option(
    "--repo-root",
    default=".",
    show_default=True,
    help="Repository root directory.",
)
def chronograph_apply(repo_root: str) -> None:
    """Stage 09: apply approved or high-confidence allowlisted actions."""
    from guardian import chronograph

    root = Path(repo_root).resolve()
    policy = chronograph.ChronographSafetyPolicy.for_repo(root)
    recommendations_path = chronograph.chronograph_path(root, "recommendations.json")
    plan_path = chronograph.chronograph_path(root, "apply-plan.json")
    results_path = chronograph.chronograph_path(root, "apply-results.json")

    actions = chronograph.load_actions(recommendations_path)
    plan = chronograph.load_apply_plan(plan_path)
    results = chronograph.apply_plan(plan, policy=policy, actions=actions)
    chronograph.write_json(results_path, results)

    applied = sum(1 for result in results if result.applied)
    click.echo(
        "Chronograph: applied "
        f"{applied}/{len(results)} plan item(s); audit log is "
        f"{policy.audit_path.relative_to(policy.repo_root)}"
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """Package entry point wired in pyproject.toml."""
    cli()
