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

The active copy is the exact policy body Guardian used or refreshed. PR review reads the base-branch version of the repo source so a PR cannot modify its own review rules. When Linear is canonical, trusted `/re-anchor` runs in a persistent checkout can fetch the configured document, normalize and hash it, then write it to the active copy. PR interviews instead use the active copy committed at the PR base SHA.

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

When `north_star.source` is `"linear"`, trusted persistent `/re-anchor` runs can fetch `linear.document_id` through Linear's GraphQL API using `LINEAR_API_KEY`. PR interviews do not receive that secret and use the active copy committed at the PR base SHA. The snapshot recorded at `.github/guardian/memory/northstar-snapshot.json` includes:

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
2. Guardian reads the PR metadata and loads both `guardian-config.json` and the review North Star from the PR base SHA, so a PR cannot redirect the policy used for its own review.
3. Guardian reads PR diff and file metadata. Files without a text patch remain in the structural context with their path and change counts, but are marked as unavailable for text review.
4. Guardian runs OpenAI-backed intent, alignment, anti-pattern, and chronicle prompts.
5. In a persistent local checkout, Guardian updates the active-copy snapshot, assigns or updates a saga, writes a journal entry, records drift and debt, and regenerates `.github/guardian/memory/dashboard.html`.
6. In GitHub Actions, the checkout is ephemeral and Guardian skips those state mutations. The workflow posts the interview report, but does not claim that state was saved between runs.
7. Guardian posts the interview report as a PR comment.
   - repo source: read the configured repo North Star from the PR base SHA
   - Linear source: read the committed active snapshot from the PR base SHA; a trusted local `/re-anchor` may fetch the current Linear document

## 6. Slash Commands

| Command | Behavior |
|---|---|
| `/init-guardian` | Provides setup instructions for `docs/northstar.md` and `.github/guardian/` |
| `/re-anchor` | Refreshes the active copy from its configured source and shows a summary |
| `/amend` | Repo-backed: update the configured repo source and active copy; Linear-backed: create a Linear amendment issue |
| `/chronicle` | Posts recent journal entries |
| `/dashboard` | Regenerates the local dashboard artifact |
| `/status` | Summarizes active debt timers and last interview |

The GitHub Actions slash-command job is limited to issue comments from repository owners, members, and collaborators. This restriction applies before the workflow exposes `LINEAR_API_KEY` for Linear-backed `/amend` requests.

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

The active-copy path must remain inside `.github/guardian/`. In ephemeral GitHub Actions runs, `/re-anchor`, repo-backed `/amend`, and `/dashboard` report that they cannot mutate Guardian state because no durable state backend is configured. Linear-backed `/amend` is allowed only for repository owners, members, and collaborators; it creates a Linear issue without writing checkout state. `sweep-debt` reports that it was skipped, while `init-local` and `preview-dashboard` reject the run because they cannot persist changes or output. Run state-mutating commands from a persistent checkout.

## 8. Invariants

- Guardian review must use a pinned policy snapshot.
- PRs must not be able to change their own review policy.
- Guardian-generated state must be visible in the normal repo checkout.
- Linear-backed policy changes must be proposed through Linear, not silently applied from PR comments.
- The default posture is advisory: record, explain, and route follow-up work without blocking merges unless explicitly configured otherwise.
