# NorthStarGuardian

An advisory GitHub Action that watches a repository drift from its declared North Star.

The Guardian runs on pull requests. It reads the repo North Star, evaluates the PR against the project identity and ranked principles, and posts a structured interview back to the PR. Guardian-owned state lives in the normal checkout under `.github/guardian/`; the human-facing North Star lives at `docs/northstar.md`.

## Status

Pre-alpha. The core pipeline is covered by tests against synthetic PR fixtures: diff analysis, OpenAI-backed interview generation, PR commenting, Linear-backed policy snapshots, repo-native Guardian memory, chronicle entries, and dashboard rendering.

## Quickstart

1. **Copy the workflow** - copy `examples/consumer-workflow.yml` into `.github/workflows/guardian.yml` in your target repo and commit it to `main`.
2. **Add secrets** - add `OPENAI_API_KEY`. If Linear is the canonical policy surface, also add `LINEAR_API_KEY`.
3. **Author the North Star** - keep the repo-visible policy at `docs/northstar.md`. You can run `guardian init-local` to create both `docs/northstar.md` and `.github/guardian/northstar.md`.
4. **Configure Guardian** - edit `.github/guardian/guardian-config.json`. Use `north_star.source = "repo"` for repo-backed policy, or `"linear"` with `linear.document_id` and `linear.team_id` for Linear-backed policy.

## What It Does On Every PR

- Reads the base-branch North Star so a PR cannot review itself against modified rules.
- If configured for Linear, fetches the Linear document, normalizes it, hashes it, and records the exact snapshot used for review.
- Posts an interview report as a PR comment: alignment summary, relevant principle verdicts, saga assignment, suggestions, and chronicle paragraph.
- Writes Guardian-owned snapshots under `.github/guardian/memory/`, including journal entries, sagas, drift/debt records, and the generated dashboard.
- Creates Linear follow-up issues for Linear-backed policy amendment requests.

## Repo Layout

```text
docs/
  northstar.md                         # human-facing repo North Star
.github/
  guardian/
    northstar.md                       # Guardian active copy / snapshot
    guardian-config.json               # runtime configuration
    memory/
      northstar-snapshot.json          # exact policy snapshot metadata
      journal/
      sagas/
      drift-ledger.json
      debt-timers.json
      dashboard.html
```

## Slash Commands

| Command | Purpose |
|---|---|
| `/init-guardian` | First-time setup instructions for `docs/northstar.md` and `.github/guardian/` |
| `/re-anchor` | Show the current active copy and point operators to the configured source |
| `/amend [principle]` | Repo-backed: update the active copy; Linear-backed: create a Linear amendment issue |
| `/chronicle` | View recent Guardian journal entries |
| `/dashboard` | Regenerate `.github/guardian/memory/dashboard.html` |
| `/status` | Quick health check: active debt timers and last interview |

## Configuration

Runtime configuration lives at `.github/guardian/guardian-config.json`.

| Field | Default | Description |
|---|---|---|
| `north_star.source` | `"repo"` | `"repo"` reads `docs/northstar.md`; `"linear"` fetches the configured Linear document |
| `north_star.repo_path` | `"docs/northstar.md"` | Repo-visible North Star path |
| `north_star.active_copy_path` | `".github/guardian/northstar.md"` | Guardian active-copy path |
| `linear.document_id` | `null` | Linear document ID used when `north_star.source` is `"linear"` |
| `linear.team_id` | `null` | Linear team ID for Guardian-created follow-up issues |
| `linear.project_id` | `null` | Optional Linear project for Guardian-created issues |
| `openai_model_analysis` | `"gpt-5"` | Model used for PR diff analysis and interview generation |
| `variance_default_days` | `7` | Default debt-timer duration when a `[VARIANCE]` tag omits an expiry |
| `pages_url` | `null` | Optional direct dashboard URL posted in comments |

`enable_blocking_escalation` defaults to `false`. The debt machinery can record expired variance timers, but merge-blocking remains opt-in because Guardian is advisory by default.

## Local Development

```powershell
python -m venv .venv
. .venv/Scripts/Activate.ps1
pip install -e ".[dev]"
guardian --help
```

## Examples

- [`examples/sample-northstar.md`](examples/sample-northstar.md) - a complete North Star for a fictional LLM pipeline project.
- [`examples/sample-dashboard.html`](examples/sample-dashboard.html) - a rendered dashboard showing all four chart types populated with sample data.
