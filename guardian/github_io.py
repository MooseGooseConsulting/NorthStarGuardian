"""GitHub I/O helpers for NorthStarGuardian.

Thin wrapper around PyGithub so ``cli.py`` stays free of API plumbing.
All GitHub context is constructed once from environment variables and the
event payload, then passed around as a ``GitHubContext``.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from github import Github, GithubException
from github.PullRequest import PullRequest
from github.Repository import Repository


@dataclass
class GitHubContext:
    """All GitHub state needed for a single workflow invocation.

    Constructed from environment variables and the event JSON payload.
    Use :func:`from_env` rather than instantiating directly.
    """

    repo: Repository
    event_name: str
    event_payload: dict[str, Any]
    pr: PullRequest | None = field(default=None)

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def from_env(cls, event_path: str | None = None) -> GitHubContext:
        """Build a :class:`GitHubContext` from the current GitHub Actions env.

        Parameters
        ----------
        event_path:
            Override for ``$GITHUB_EVENT_PATH``.  Useful in tests.
        """
        token = os.environ["GITHUB_TOKEN"]
        repo_name = os.environ["GITHUB_REPOSITORY"]  # "owner/repo"
        event_name = os.environ.get("GITHUB_EVENT_NAME", "")

        path = event_path or os.environ.get("GITHUB_EVENT_PATH", "")
        if path:
            payload: dict[str, Any] = json.loads(Path(path).read_text(encoding="utf-8"))
        else:
            payload = {}

        gh = Github(token)
        repo = gh.get_repo(repo_name)

        pr: PullRequest | None = None

        if event_name == "pull_request":
            pr_number = payload.get("pull_request", {}).get("number")
            if pr_number is not None:
                pr = repo.get_pull(int(pr_number))

        return cls(
            repo=repo,
            event_name=event_name,
            event_payload=payload,
            pr=pr,
        )


# ---------------------------------------------------------------------------
# PR comment
# ---------------------------------------------------------------------------


def post_pr_comment(ctx: GitHubContext, body: str) -> None:
    """Post *body* as a comment on ``ctx.pr``.

    Raises ``RuntimeError`` if the context has no associated PR.
    """
    if ctx.pr is None:
        raise RuntimeError("Cannot post a PR comment: no PR in context")
    ctx.pr.create_issue_comment(body)


# ---------------------------------------------------------------------------
# PR diff
# ---------------------------------------------------------------------------


def get_pr_diff(ctx: GitHubContext, files: list[Any] | None = None) -> str:
    """Return the unified diff for ``ctx.pr`` as a string.

    Uses the PyGithub ``files`` endpoint to reconstruct a unified diff.
    Raises ``RuntimeError`` if no PR is present in the context.
    """
    if ctx.pr is None:
        raise RuntimeError("Cannot fetch diff: no PR in context")

    parts: list[str] = []
    for f in files if files is not None else ctx.pr.get_files():
        header = f"diff --git a/{f.filename} b/{f.filename}\n"
        if f.patch:
            parts.append(header + f.patch)
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# PR metadata
# ---------------------------------------------------------------------------


def get_pr_meta(ctx: GitHubContext) -> dict[str, Any]:
    """Return a dict of PR metadata useful for ``analyze_diff``.

    Raises ``RuntimeError`` if no PR is present in the context.
    """
    if ctx.pr is None:
        raise RuntimeError("Cannot fetch PR metadata: no PR in context")

    pr = ctx.pr
    return {
        "number": pr.number,
        "title": pr.title,
        "body": pr.body or "",
        "author": pr.user.login if pr.user else "unknown",
        "base_sha": pr.base.sha,
        "head_sha": pr.head.sha,
        "base_ref": pr.base.ref,
        "head_ref": pr.head.ref,
        "url": pr.html_url,
    }
