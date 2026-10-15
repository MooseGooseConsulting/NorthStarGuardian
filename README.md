# NorthStarGuardian

An advisory GitHub Action that watches a repository drift from its declared North Star.

The Guardian runs on pull requests. It reads the repo North Star, evaluates the PR against the project identity and ranked principles, and posts a structured interview back to the PR. Guardian-owned state lives in the normal checkout under `.github/guardian/`; the human-facing North Star lives at `docs/northstar.md`.

## Status

Pre-alpha. The core pipeline is covered by tests against synthetic PR fixtures: diff analysis, OpenAI-backed interview generation, PR commenting, Linear-backed policy snapshots, repo-native Guardian memory, chronicle entries, and dashboard rendering.

## Quickstart

1. **Copy the workflow** - copy `examples/consumer-workflow.yml` into `.github/workflows/guardian.yml` in your target repo and commit it to `main`.
2. **Add secrets** - add `OPENAI_API_KEY`. If Linear is the canonical policy surface, add `LINEAR_API_KEY` for trusted slash-command operations that refresh the active snapshot or create follow-up issues. Pull request interviews use the committed active snapshot from the base commit and do not receive the Linear secret.
3. **Author the North Star** - keep the repo-visible policy at `docs/northstar.md`. You can run `guardian init-local` to create both `docs/northstar.md` and `.github/guardian/northstar.md`.
4. **Configure Guardian** - edit `.github/guardian/guardian-config.json`. Use `north_star.source = "repo"` for repo-backed policy, or `"linear"` with `linear.document_id` and `linear.team_id` for Linear-backed policy.

For Linear-backed policy, PR interviews use the committed active-copy snapshot at the PR base and cannot refresh Linear from ephemeral GitHub Actions. Refresh and commit the active copy from a trusted persistent checkout (or another supported process) before reviews need the new policy.

## What It Does On Every PR

- Reads the base-branch North Star so a PR cannot review itself against modified rules.
- If configured for Linear, PR reviews use the active snapshot committed on the base branch. A trusted `/re-anchor` in a persistent checkout can refresh it from Linear.
- Posts an interview report as a PR comment with the alignment summary, relevant principle verdicts, suggestions, and chronicle paragraph. Persistent local runs also assign a saga; ephemeral GitHub Actions runs skip repo-native state mutations.
- Persistent local runs write Guardian-owned state under `.github/guardian/memory/`, including journal entries, sagas, drift/debt records, and the generated dashboard. GitHub-hosted Actions runs use ephemeral checkouts and skip these mutations; use a persistent checkout to record state.
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
| `/re-anchor` | Refresh the active copy from its configured source and show a summary |
| `/amend [principle]` | Repo-backed: update the configured repo source and active copy; Linear-backed: create a Linear amendment issue |
| `/chronicle` | View recent Guardian journal entries |
| `/dashboard` | Regenerate `.github/guardian/memory/dashboard.html` |
| `/status` | Quick health check: active debt timers and last interview |

## Automation

While Guardian is designed to be triggered via GitHub Actions, the CLI can be run manually or as scheduled jobs.

In GitHub Actions, the slash-command job runs only for comments from repository owners, members, or collaborators. Linear-backed `/amend` can create an issue for those users; repo-backed state changes remain disabled in ephemeral checkouts.

| CLI Command | Purpose |
|---|---|
| `guardian interview` | Triggered by `pull_request` events to analyze diff and post comments. |
| `guardian command` | Triggered by `issue_comment` events to handle slash commands. |
| `guardian sweep-debt` | Checks debt timers and escalates them in a persistent checkout; GitHub Actions runs skip mutations because their checkouts are ephemeral. |
| `guardian preview-dashboard` | Local dev: render the dashboard to a local HTML file. |

## Configuration

Runtime configuration lives at `.github/guardian/guardian-config.json`.

| Field | Default | Description |
|---|---|---|
| `north_star.source` | `"repo"` | `"repo"` reads `docs/northstar.md`; `"linear"` uses the committed active snapshot in PR interviews and can fetch the configured Linear document in trusted slash-command runs |
| `north_star.repo_path` | `"docs/northstar.md"` | Repo-visible North Star path |
| `north_star.active_copy_path` | `".github/guardian/northstar.md"` | Guardian active-copy path |
| `linear.document_id` | `null` | Linear document ID used when `north_star.source` is `"linear"` |
| `linear.team_id` | `null` | Linear team ID for Guardian-created follow-up issues |
| `linear.project_id` | `null` | Optional Linear project for Guardian-created issues |
| `openai_model_analysis` | `"gpt-5"` | Model used for PR diff analysis and interview generation |
| `openai_model_initialization` | `"gpt-5"` | Model used for North Star initialization |
| `variance_default_days` | `7` | Default debt-timer duration when a `[VARIANCE]` tag omits an expiry |
| `pages_url` | `null` | Optional direct dashboard URL posted in comments |

Legacy flat North Star and Linear settings are normalized into the nested `north_star` and `linear` sections. Explicit nested values take precedence when both forms are present.

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
