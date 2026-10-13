# NorthStarGuardian Specification

## 1. Product Shape

NorthStarGuardian is an advisory repository review agent. It runs from GitHub Actions, reads a repo North Star, evaluates pull requests against that policy, and posts a PR interview explaining whether the change reinforces or dilutes the project's identity.

Guardian is not a merge authority by default. It should make drift visible, create follow-up records, and preserve review context without silently rewriting the rules it evaluates.

## 2. North Star Sources

Every protected repository must contain:

```text
docs/northstar.md
```

That file is the human-facing repo North Star. It contains the project identity statement, ranked principles, approved architecture, and anti-patterns.

Guardian also maintains an active copy:

```text
.github/guardian/northstar.md
```

The active copy is the exact policy body Guardian used or refreshed. PR review reads the base-branch version of the repo source so a PR cannot modify its own review rules. When Linear is configured as the canonical surface, Guardian fetches the configured Linear document, normalizes it, hashes it, and writes the fetched content to the active copy before review.

## 3. Guardian State

Guardian-owned state lives in the normal checkout:

```text
.github/guardian/
  guardian-config.json
  northstar.md
  memory/
    northstar-snapshot.json
    amendment-log.md
    journal/
    sagas/
      _index.json
    drift-ledger.json
    debt-timers.json
    dashboard.html
```

The `.github/guardian/` tree is tracked repo content. It is not hidden local state, and it does not require a separate Git branch. The storage layer treats this directory as Guardian's local state root and exposes `read`, `write`, `exists`, `list`, `read_json`, `write_json`, and `session`.

The filesystem backend writes to the current checkout only. Local runs retain
those files on disk; GitHub-hosted workflow checkouts are ephemeral and do not
retain state between runs unless the workflow configures a durable persistence
mechanism.

## 4. Linear Integration

Linear is the first external planning and memory integration.

When `north_star.source` is `"linear"`, Guardian fetches `linear.document_id` through Linear's GraphQL API using `LINEAR_API_KEY`. The snapshot recorded at `.github/guardian/memory/northstar-snapshot.json` includes:

- provider
- document ID
- document content ID when available
- URL
- title
- updated timestamp
- updater
- fetch timestamp
- SHA-256 hash
- normalized content

Guardian may use Linear issues for concrete follow-ups such as policy amendment proposals and drift tasks. Linear is allowed to be the canonical managed planning surface, but Guardian must still record the exact snapshot it used for each review.

## 5. PR Interview Flow

1. GitHub Actions runs `guardian interview` for PR events.
2. Guardian initializes `.github/guardian/`.
3. Guardian loads `guardian-config.json`, then creates the OpenAI client.
4. Guardian reads PR diff and metadata.
5. Guardian loads the review North Star:
   - repo source: read `docs/northstar.md` from the PR base SHA
   - Linear source: read the committed active snapshot from the PR base SHA; trusted slash-command runs can refresh it from the configured Linear document
6. Guardian writes the active copy and snapshot metadata.
7. Guardian analyzes the diff structurally.
8. Guardian runs OpenAI-backed intent, alignment, anti-pattern, and chronicle prompts.
9. Guardian assigns or updates a saga and writes a journal entry.
10. Guardian regenerates `.github/guardian/memory/dashboard.html`.
11. Guardian posts the interview report as a PR comment.

## 6. Slash Commands

| Command | Behavior |
|---|---|
| `/init-guardian` | Provides setup instructions for `docs/northstar.md` and `.github/guardian/` |
| `/re-anchor` | Shows the current active copy and points operators to the configured source |
| `/amend` | Repo-backed: update active copy; Linear-backed: create a Linear amendment issue |
| `/chronicle` | Posts recent journal entries |
| `/dashboard` | Regenerates the local dashboard artifact |
| `/status` | Summarizes active debt timers and last interview |

## 7. Configuration

Configuration lives at:

```text
.github/guardian/guardian-config.json
```

Important fields:

- `north_star.source`: `"repo"` or `"linear"`
- `north_star.repo_path`: defaults to `docs/northstar.md`
- `north_star.active_copy_path`: defaults to `.github/guardian/northstar.md`
- `linear.document_id`: Linear document for canonical policy
- `linear.team_id`: Linear team for created follow-up issues
- `linear.project_id`: optional Linear project for created follow-up issues
- `openai_model_analysis`: model for review prompts
- `variance_default_days`: default duration for declared variance debt
- `enable_blocking_escalation`: defaults to `false`
- `pages_url`: optional direct dashboard URL

## 8. Invariants

- Guardian review must use a pinned policy snapshot.
- PRs must not be able to change their own review policy.
- Guardian-generated state must be visible in the normal repo checkout.
- Linear-backed policy changes must be proposed through Linear, not silently applied from PR comments.
- The default posture is advisory: record, explain, and route follow-up work without blocking merges unless explicitly configured otherwise.
