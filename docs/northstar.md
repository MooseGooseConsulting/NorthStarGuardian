---
{
  "version": 1,
  "project_name": "NorthStarGuardian",
  "identity_statement": "NorthStarGuardian is an advisory GitHub review agent that evaluates pull requests against a repository's declared North Star. It is not a merge gate by default, a hidden storage system, or a replacement for human product judgment.",
  "approved_architecture": "Guardian runs from GitHub Actions through the `guardian` CLI. Python modules under `guardian/` own domain behavior, OpenAI-backed prompts perform semantic review, Linear integration handles optional external policy and follow-up routing, and Guardian-owned artifacts are visible under `.github/guardian/`.",
  "created_at": "2026-05-26T00:00:00+00:00",
  "principles": [
    {
      "id": "p1",
      "rank": 1,
      "text": "Review against a pinned North Star snapshot so pull requests cannot rewrite the policy used to evaluate themselves."
    },
    {
      "id": "p2",
      "rank": 2,
      "text": "Keep Guardian advisory by default: record, explain, and route follow-up work without silently becoming a merge authority."
    },
    {
      "id": "p3",
      "rank": 3,
      "text": "Make Guardian state inspectable in the normal repository checkout under `.github/guardian/`."
    },
    {
      "id": "p4",
      "rank": 4,
      "text": "Prefer GitHub-native review flow and operator commands over local-only or hidden automation."
    },
    {
      "id": "p5",
      "rank": 5,
      "text": "Use Linear as an optional managed policy and follow-up surface while preserving the exact snapshot used for every review."
    }
  ],
  "anti_patterns": [
    {
      "id": "ap1",
      "description": "Reintroducing hidden storage branches, detached checkouts, or local-only state as the source of Guardian truth."
    },
    {
      "id": "ap2",
      "description": "Letting a pull request evaluate itself against policy content that only exists on the PR head."
    },
    {
      "id": "ap3",
      "description": "Treating advisory drift records as hard merge blocks without explicit opt-in configuration."
    }
  ]
}
---
# NorthStarGuardian — North Star

## Identity

NorthStarGuardian is an advisory GitHub review agent that evaluates pull requests against a repository's declared North Star. It is not a merge gate by default, a hidden storage system, or a replacement for human product judgment.

## Principles

### 1. Review against a pinned North Star snapshot so pull requests cannot rewrite the policy used to evaluate themselves.

### 2. Keep Guardian advisory by default: record, explain, and route follow-up work without silently becoming a merge authority.

### 3. Make Guardian state inspectable in the normal repository checkout under `.github/guardian/`.

### 4. Prefer GitHub-native review flow and operator commands over local-only or hidden automation.

### 5. Use Linear as an optional managed policy and follow-up surface while preserving the exact snapshot used for every review.

## Approved Architecture

Guardian runs from GitHub Actions through the `guardian` CLI. Python modules under `guardian/` own domain behavior, OpenAI-backed prompts perform semantic review, Linear integration handles optional external policy and follow-up routing, and Guardian-owned artifacts are visible under `.github/guardian/`.

## Anti-Patterns

### ap1

Reintroducing hidden storage branches, detached checkouts, or local-only state as the source of Guardian truth.

### ap2

Letting a pull request evaluate itself against policy content that only exists on the PR head.

### ap3

Treating advisory drift records as hard merge blocks without explicit opt-in configuration.
