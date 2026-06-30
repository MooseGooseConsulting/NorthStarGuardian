# ColdTrace Branch Triage — 2026-05-26

**Repo:** `Coldaine/ColdTrace`
**Main SHA:** `f3bf7267`
**Total non-main branches:** 24
**Triage date:** 2026-05-26

---

## Methodology

For each branch:
1. `gh api repos/Coldaine/ColdTrace/compare/main...<branch>` → `ahead_by` / `behind_by`
2. All PRs (state=all) correlated by head branch name
3. Unique commit SHAs verified against main via `compare/<sha>...main` (if `main_ahead > 0` and `sha_ahead == 0`, the commit is an ancestor of main → content already on main)

**Key finding:** GitHub's merge-base compare shows `ahead_by > 0` for many branches even when their unique commits are actually reachable from main (squash-merges via salvage/land branches create this illusion). All commits were cross-checked directly.

---

## Tier A — Safe to Delete (auto-approvable)

Criteria: unique commit content already on main, AND (merged PR OR closed-unmerged with no novel content OR no PR with no novel content).

| # | Branch | Head SHA | Author | Last Active | PR | Notes |
|---|--------|----------|--------|-------------|-----|-------|
| 1 | `convoy/coldtrace-advanced-ui-devexp/30996ee3/head` | `f659031f` | Patrick MacLyman | 2026-03-24 | None | ahead=0, pure orphan placeholder |
| 2 | `convoy/coldtrace-design-ratification-progressiv/d263914d/gt/shadow/add34fbc` | `f659031f` | Patrick MacLyman | 2026-03-24 | None | ahead=0, same placeholder SHA as above |
| 3 | `convoy/coldtrace-foundations-progressive-disclo/e123217c/head` | `f659031f` | Patrick MacLyman | 2026-03-24 | None | ahead=0, placeholder |
| 4 | `convoy/coldtrace-high-priority-mvp-tasks/62c12535/head` | `f659031f` | Patrick MacLyman | 2026-03-24 | None | ahead=0, placeholder |
| 5 | `convoy/coldtrace-intelligence-layer-foundation/52dd3d5f/head` | `f659031f` | Patrick MacLyman | 2026-03-24 | None | ahead=0, placeholder |
| 6 | `convoy/coldtrace-llm-enhanced-analyzers/adbe90f8/head` | `f659031f` | Patrick MacLyman | 2026-03-24 | None | ahead=0, placeholder |
| 7 | `convoy/coldtrace-low-priority-enhancements-bug-/0aeb22e8/head` | `f659031f` | Patrick MacLyman | 2026-03-24 | None | ahead=0, placeholder |
| 8 | `convoy/coldtrace-progressive-disclosure-search-/9ca3a2da/head` | `f659031f` | Patrick MacLyman | 2026-03-24 | None | ahead=0, placeholder |
| 9 | `convoy/coldtrace-viewer-resilience-testing/c9c5fdc3/head` | `f659031f` | Patrick MacLyman | 2026-03-24 | None | ahead=0, placeholder |
| 10 | `convoy/coldtrace-design-ratification-progressiv/d263914d/head` | `307262d9` | refinery (gastown) | 2026-04-23 | PR #11 closed (unmerged) | 3 unique commits — all verified on main via PR #12 land |
| 11 | `feat/convoy-d263914d-land` | `e379480a` | refinery (gastown) | 2026-04-23 | **PR #12 MERGED** 2026-04-23 | 4 commits — all on main |
| 12 | `salvage/fileloader-errorbanner-integration` | `db074831` | mayor (gastown) | 2026-04-23 | **PR #13 MERGED** 2026-04-23 | 5 commits — all on main |
| 13 | `convoy/coldtrace-progressive-disclosure-foundat/78dd7a06/gt/birch/14b2a48a` | `eafdd4a7` | GASTOWN | 2026-04-23 | **PR #16 MERGED** 2026-04-23 | 1 commit — verified on main |
| 14 | `convoy/coldtrace-progressive-disclosure-foundat/78dd7a06/head` | `13e094de` | GASTOWN | 2026-04-23 | None (convoy head; landed via PR #16) | 2 commits — both on main |
| 15 | `convoy/coldtrace-design-ratification-progressiv/d263914d/gt/maple/0c9b9a5d` | `27db6e7b` | Maple (gastown) | 2026-04-23 | None (gt sub-branch) | 1 commit — verified on main |
| 16 | `convoy/coldtrace-design-ratification-progressiv/d263914d/gt/toast/7c077e49` | `d9f7c88f` | Toast (gastown) | 2026-04-23 | None (gt sub-branch) | 1 commit — verified on main |
| 17 | `convoy/coldtrace-foundations-progressive-disclo/e123217c/gt/toast/ef953253` | `dee25ef8` | Toast (gastown) | 2026-04-23 | None (gt sub-branch) | 1 commit — verified on main |
| 18 | `convoy/coldtrace-sidepanel-fileloader-errorbann/5009c9ed/gt/birch/b8689387` | `7a2e609b` | Maple (gastown) | 2026-04-23 | None (gt sub-branch) | 1 commit — verified on main |
| 19 | `convoy/coldtrace-sidepanel-fileloader-errorbann/5009c9ed/head` | `7a2e609b` | Maple (gastown) | 2026-04-23 | None (convoy head; same SHA as gt branch above) | 1 commit — verified on main |
| 20 | `gt/birch/b50ad7a9` | `19696ba8` | GASTOWN | 2026-04-23 | None (gt orphan) | 1 commit ("focus mode") — verified on main |
| 21 | `gt/birch/0c16042c` | `691fda3b` | Birch (gastown) | 2026-04-23 | None (gt orphan) | 2 commits (FileLoader extract + design decision) — both on main |
| 22 | `agentic-foundations` | `5a8e3a6c` | GASTOWN | 2026-04-25 | None | 1 commit ("agentic foundations ref docs") — verified on main |

**Tier A count: 22 branches**

### Copy-paste delete commands (Tier A)

```bash
gh api repos/Coldaine/ColdTrace/git/refs/heads/convoy%2Fcoldtrace-advanced-ui-devexp%2F30996ee3%2Fhead --method DELETE
gh api repos/Coldaine/ColdTrace/git/refs/heads/convoy%2Fcoldtrace-design-ratification-progressiv%2Fd263914d%2Fgt%2Fshadow%2Fadd34fbc --method DELETE
gh api repos/Coldaine/ColdTrace/git/refs/heads/convoy%2Fcoldtrace-foundations-progressive-disclo%2Fe123217c%2Fhead --method DELETE
gh api repos/Coldaine/ColdTrace/git/refs/heads/convoy%2Fcoldtrace-high-priority-mvp-tasks%2F62c12535%2Fhead --method DELETE
gh api repos/Coldaine/ColdTrace/git/refs/heads/convoy%2Fcoldtrace-intelligence-layer-foundation%2F52dd3d5f%2Fhead --method DELETE
gh api repos/Coldaine/ColdTrace/git/refs/heads/convoy%2Fcoldtrace-llm-enhanced-analyzers%2Fadbe90f8%2Fhead --method DELETE
gh api repos/Coldaine/ColdTrace/git/refs/heads/convoy%2Fcoldtrace-low-priority-enhancements-bug-%2F0aeb22e8%2Fhead --method DELETE
gh api repos/Coldaine/ColdTrace/git/refs/heads/convoy%2Fcoldtrace-progressive-disclosure-search-%2F9ca3a2da%2Fhead --method DELETE
gh api repos/Coldaine/ColdTrace/git/refs/heads/convoy%2Fcoldtrace-viewer-resilience-testing%2Fc9c5fdc3%2Fhead --method DELETE
gh api repos/Coldaine/ColdTrace/git/refs/heads/convoy%2Fcoldtrace-design-ratification-progressiv%2Fd263914d%2Fhead --method DELETE
gh api repos/Coldaine/ColdTrace/git/refs/heads/feat%2Fconvoy-d263914d-land --method DELETE
gh api repos/Coldaine/ColdTrace/git/refs/heads/salvage%2Ffileloader-errorbanner-integration --method DELETE
gh api repos/Coldaine/ColdTrace/git/refs/heads/convoy%2Fcoldtrace-progressive-disclosure-foundat%2F78dd7a06%2Fgt%2Fbirch%2F14b2a48a --method DELETE
gh api repos/Coldaine/ColdTrace/git/refs/heads/convoy%2Fcoldtrace-progressive-disclosure-foundat%2F78dd7a06%2Fhead --method DELETE
gh api repos/Coldaine/ColdTrace/git/refs/heads/convoy%2Fcoldtrace-design-ratification-progressiv%2Fd263914d%2Fgt%2Fmaple%2F0c9b9a5d --method DELETE
gh api repos/Coldaine/ColdTrace/git/refs/heads/convoy%2Fcoldtrace-design-ratification-progressiv%2Fd263914d%2Fgt%2Ftoast%2F7c077e49 --method DELETE
gh api repos/Coldaine/ColdTrace/git/refs/heads/convoy%2Fcoldtrace-foundations-progressive-disclo%2Fe123217c%2Fgt%2Ftoast%2Fef953253 --method DELETE
gh api repos/Coldaine/ColdTrace/git/refs/heads/convoy%2Fcoldtrace-sidepanel-fileloader-errorbann%2F5009c9ed%2Fgt%2Fbirch%2Fb8689387 --method DELETE
gh api repos/Coldaine/ColdTrace/git/refs/heads/convoy%2Fcoldtrace-sidepanel-fileloader-errorbann%2F5009c9ed%2Fhead --method DELETE
gh api repos/Coldaine/ColdTrace/git/refs/heads/gt%2Fbirch%2Fb50ad7a9 --method DELETE
gh api repos/Coldaine/ColdTrace/git/refs/heads/gt%2Fbirch%2F0c16042c --method DELETE
gh api repos/Coldaine/ColdTrace/git/refs/heads/agentic-foundations --method DELETE
```

---

## Tier B — Probably Delete (needs eyes)

Criteria: orphan (no PR), unique commits, but content verified on main.

*All orphan branches with unique content were already verified as reachable from main (see Tier A rows 20–22 and 14–19). No branches fall into a "content looks novel but unreviewed" category — everything with unique commits was absorbed.*

**Tier B count: 0 branches**

---

## Tier C — Keep / Investigate

| # | Branch | Head SHA | Author | Last Active | PR | Notes |
|---|--------|----------|--------|-------------|-----|-------|
| 1 | `docs/reorganization-2026-05` | `f8db54b6` | Coldaine | **2026-05-26** | **PR #29 OPEN** | 8 unique commits ahead of main, active open PR — do not touch |
| 2 | `codex/implement-annotated-callouts-ui/2026-03-25` | `232a88c6` | Coldaine | 2026-03-25 | None | 1 commit ("annotated callouts demo + inspector scaffold") ahead=1, behind=22 — content is on main per SHA check, but this is a Codex-authored feature branch with no PR; low risk to delete but worth a 10-second look |

**Tier C count: 2 branches**

### Notes on Tier C

- `docs/reorganization-2026-05` — **active, open PR #29 as of today**. Do not delete.
- `codex/implement-annotated-callouts-ui/2026-03-25` — Stale Codex branch from March 2026. The commit `232a88c6` is verified on main. Technically safe to delete (Tier A-eligible), but it was authored by `Coldaine` (the human account, not a bot), so flagging it for a quick eye check before pulling the trigger. If you're comfortable the annotated-callouts work landed, move it to Tier A.

---

## Summary

| Tier | Count | Action |
|------|-------|--------|
| A — Safe to delete | 22 | Copy-paste commands above |
| B — Probably delete | 0 | n/a |
| C — Keep / investigate | 2 | Keep `docs/reorganization-2026-05`; review `codex/implement-annotated-callouts-ui/2026-03-25` |

**Branches with the SHA `f659031f` (9 branches)** are pure bot-created convoy placeholders — they point to a March 2026 commit by Patrick that predates all convoy work. They have no PR and zero unique content.

The "ahead_by > 0" readings from `compare/main...<branch>` were misleading for all 22 Tier A branches — GitHub reports divergence from merge-base, not ancestry. Direct SHA ancestry checks confirmed every unique commit is already reachable from main.
