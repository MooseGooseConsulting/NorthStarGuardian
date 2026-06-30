"""End-to-end smoke test: analyze_diff → run_interview → assign_saga →
update_saga → write_journal_entry → render_dashboard against a real
MemoryStore (tmp_path git repo + bare local remote) with a mocked LLM client.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from guardian.analyze import analyze_diff, run_interview
from guardian.chronicle import assign_saga, update_saga, write_journal_entry
from guardian.dashboard import render_dashboard
from guardian.memory import MemoryStore
from guardian.north_star import initialize_north_star, write_north_star


def _make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "README.md").write_text("# test repo\n", encoding="utf-8")
    return repo


# 2 context + 1 removed + 2 added → @@ -1,3 +1,4 @@

SAMPLE_DIFF = """\
--- a/guardian/analyze.py
+++ b/guardian/analyze.py
@@ -1,3 +1,4 @@
 # existing line
-old_code()
+new_code()
+import os
 # end
"""

PR_META: dict[str, Any] = {
    "number": 42,
    "title": "Refactor analyze module",
    "body": "Cleans up the analyze module internals.",
    "author": "alice",
    "base_sha": "abc123",
    "head_sha": "def456",
    "commit_messages": ["refactor: clean up analyze internals"],
}


def _llm_response(text: str) -> MagicMock:
    """Mock openai response whose .choices[0].message.content == text."""
    block = MagicMock()
    block.content = text
    choice = MagicMock()
    choice.message = block
    resp = MagicMock()
    resp.choices = [choice]
    return resp


def _build_mock_client(principle_ids: list[str], verdict: str = "aligned") -> MagicMock:
    """Mock openai client driving the 5 LLM calls of run_interview + assign_saga.

    `verdict` controls what evaluate_alignment returns for the (one) relevant
    principle: "aligned", "drift", or "ambiguous".
    """
    intent_json = json.dumps(
        {
            "one_line": "Refactored the analyze module.",
            "paragraph": "The PR refactored the analyze module by swapping helpers.",
        }
    )
    alignment_json = json.dumps(
        [
            {
                "principle_id": pid,
                "relevant": (i == 0),
                "verdict": verdict if i == 0 else None,
                "reasoning": f"Verdict reasoning for {verdict}." if i == 0 else None,
                "citations": ["guardian/analyze.py:2"] if i == 0 else [],
            }
            for i, pid in enumerate(principle_ids)
        ]
    )
    chronicle_prose = f"PR #42 was assessed as {verdict}."

    client = MagicMock()
    client.chat.completions.create.side_effect = [
        _llm_response(intent_json),
        _llm_response(alignment_json),
        _llm_response("[]"),
        _llm_response(chronicle_prose),
        _llm_response("[]"),
        _llm_response("CREATE: New Saga"),
    ]
    return client


@pytest.fixture()
def repo_and_store(tmp_path: Path) -> tuple[Path, MemoryStore]:
    """Real filesystem-backed Guardian store seeded with an active North Star."""
    repo = _make_repo(tmp_path)
    store = MemoryStore(repo)
    store.ensure_initialized()

    north_star = initialize_north_star(
        {
            "project_name": "TestProject",
            "identity_statement": (
                "TestProject is an LLM-powered analysis pipeline. "
                "It is not a collection of standalone scripts."
            ),
            "principles": [
                "All analysis flows through the LLM layer.",
                "Prefer functional composition over mutable state.",
                "No standalone scripts that bypass the LLM.",
                "All public functions are type-annotated.",
                "Tests mock LLM calls; no real network access in CI.",
            ],
            "approved_architecture": (
                "Python 3.11+, Pydantic v2 models, OpenAI SDK for LLM calls, "
                "Jinja2 for templating, unidiff for diff parsing."
            ),
            "anti_patterns": [
                {
                    "id": "ap1",
                    "description": "Standalone scripts that bypass the LLM layer.",
                    "example": "scripts/run_analysis.sh",
                },
            ],
        },
        actor="test-setup",
    )
    write_north_star(store, north_star, rationale="Initial North Star for E2E test")

    return repo, store


@pytest.mark.parametrize("verdict", ["aligned", "ambiguous", "drift"])
def test_full_pr_interview_cycle(
    repo_and_store: tuple[Path, MemoryStore],
    verdict: str,
) -> None:
    from guardian.north_star import read_north_star

    _repo, store = repo_and_store
    diff_analysis = analyze_diff(SAMPLE_DIFF, PR_META)
    assert diff_analysis.pr_number == 42
    assert len(diff_analysis.files) == 1

    north_star = read_north_star(store)
    principle_ids = [p.id for p in north_star.principles]
    assert len(principle_ids) == 5

    client = _build_mock_client(principle_ids, verdict=verdict)

    report = run_interview(
        diff_analysis,
        north_star,
        client=client,
        model="claude-test-mock",
        pr_meta=PR_META,
    )
    assert report.pr_number == 42
    assert report.overall_verdict.value == verdict
    assert report.chronicle_paragraph
    assert report.intent.one_line
    assert client.chat.completions.create.call_count == 5

    saga = assign_saga(
        store,
        report.intent,
        existing_sagas=[],
        client=client,
        model="claude-test-mock",
    )
    assert client.chat.completions.create.call_count == 6
    assert saga.id
    assert saga.name == "New Saga"

    saga = update_saga(store, saga, report.pr_number)
    assert report.pr_number in saga.pr_numbers

    report = report.model_copy(update={"saga_id": saga.id})
    entry = write_journal_entry(store, report, saga)
    assert entry.pr_number == 42
    assert entry.saga_id == saga.id

    html = render_dashboard(store, north_star)
    assert html

    journal_files = store.list("memory/journal/")
    assert journal_files, "no journal files materialised"
    raw_journal = store.read(journal_files[0])
    parts = raw_journal.split("---", 2)
    assert len(parts) == 3, f"missing YAML frontmatter delimiters: {raw_journal[:200]}"
    fm: dict[str, Any] = {}
    for line in parts[1].strip().splitlines():
        if ":" in line:
            key, _, val = line.partition(":")
            fm[key.strip()] = val.strip()
    for expected in ("pr_number", "verdict", "saga_id", "timestamp"):
        assert expected in fm, f"frontmatter missing {expected}: {fm}"
    assert int(fm["pr_number"]) == 42

    assert store.exists("memory/sagas/_index.json")
    saga_index = store.read_json("memory/sagas/_index.json")
    assert saga.id in [s["id"] for s in saga_index["sagas"]]

    assert store.exists("memory/dashboard.html")
    dashboard_content = store.read("memory/dashboard.html")
    assert "cdn.jsdelivr.net" in dashboard_content
    for directive in ("gantt", "gitGraph", "quadrantChart", "mindmap"):
        assert directive in dashboard_content, f"chart directive {directive!r} missing"


def test_integrated_interview_workflow_with_governance(
    repo_and_store: tuple[Path, MemoryStore]
) -> None:
    from guardian import governance
    from guardian.models import DriftSeverity, Verdict
    from guardian.cli import _format_report_comment, GuardianConfig
    from guardian.north_star import read_north_star

    _repo, store = repo_and_store
    config = GuardianConfig()

    pr_meta = {
        "number": 99,
        "title": "Introduce standalone scripting and mock variance",
        "body": "This PR breaks the LLM rule but has [VARIANCE: p1 | needed for testing | 10 days].",
        "author": "bob",
        "base_sha": "abc111",
        "head_sha": "def222",
    }

    diff_analysis = analyze_diff(SAMPLE_DIFF, pr_meta)

    # 1. Intent JSON
    intent_json = json.dumps(
        {
            "one_line": "Added Standalone script and variance",
            "paragraph": "This is Bob's change.",
        }
    )
    # 2. Alignment JSON - Return DRIFT on Principle 1 (which we'll ID as 'p1')
    alignment_json = json.dumps(
        [
            {
                "principle_id": "p1",
                "relevant": True,
                "verdict": "drift",
                "reasoning": "Stand-alone script that runs without any LLM call.",
                "citations": ["scripts/bad.py:10"],
            }
        ]
    )
    # 3. Anti-Patterns Matches JSON
    ap_match_json = json.dumps(
        [
            {
                "pattern_id": "ap1",
                "location": "scripts/bad.py:1",
                "explanation": "standalone shell script bypasses LLM",
            }
        ]
    )

    client = MagicMock()
    client.chat.completions.create.side_effect = [
        _llm_response(intent_json),      # assess_intent
        _llm_response(alignment_json),   # evaluate_alignment
        _llm_response(ap_match_json),    # detect_anti_patterns
        _llm_response("Chronicle summary for bob."), # _draft_chronicle
        _llm_response('["Consider using a helper class instead"]'), # _draft_suggestions
        _llm_response("CREATE: bob-saga"), # assign_saga
    ]

    north_star = read_north_star(store)

    report = run_interview(
        diff_analysis,
        north_star,
        client=client,
        model="claude-test-mock",
        pr_meta=pr_meta,
    )

    assert report.pr_number == 99
    assert report.overall_verdict == Verdict.DRIFT
    assert len(report.intent.declared_variances) == 1
    assert report.intent.declared_variances[0].principle_id == "p1"

    # Assign saga & Chronicle
    saga = assign_saga(
        store,
        report.intent,
        existing_sagas=[],
        client=client,
        model="claude-test-mock",
    )
    saga = update_saga(store, saga, report.pr_number)
    report = report.model_copy(update={"saga_id": saga.id})
    write_journal_entry(store, report, saga)

    # 4. Governance & Drift Logging (simulating cli.py)
    # Log drift for specific principle evaluations.
    for pe in report.principle_evaluations:
        if pe.verdict == Verdict.DRIFT:
            severity = governance.map_drift_severity("drift")
            governance.log_drift(
                store,
                pr_number=report.pr_number,
                principle_id=pe.principle_id,
                severity=severity,
                details=pe.reasoning or "Principle evaluation returned drift.",
            )

    # Log drift for detected anti-patterns.
    for apm in report.anti_pattern_matches:
        severity = governance.map_drift_severity("anti-pattern")
        governance.log_drift(
            store,
            pr_number=report.pr_number,
            principle_id="anti-pattern",
            severity=severity,
            details=f"Detected pattern: {apm.pattern_id}. {apm.explanation}",
        )

    # Grant variances
    for tag in report.intent.declared_variances:
        governance.grant_variance(
            store,
            tag,
            pr_number=report.pr_number,
            config=config,
            affected_paths=["scripts/bad.py"],
        )

    # Refresh dashboard
    html = render_dashboard(store, north_star)

    # Build PR comment
    comment = _format_report_comment(report, config)

    # Assertions
    # A. Comment assertions
    assert "Guardian Interview — PR #99" in comment
    assert "Added Standalone script and variance" in comment or "refactor" in comment.lower() or "Bob" in comment or "Chronicle" in comment
    assert "scripts/bad.py:1" in comment

    # B. Journal entry assertions
    journal_files = store.list("memory/journal/")
    assert any("99" in f for f in journal_files)

    # C. Drift ledger assertions
    assert store.exists("memory/drift-ledger.json")
    drift_ledger = store.read_json("memory/drift-ledger.json")
    # Should contain 2 events: one for drift evaluation, one for anti-pattern
    assert len(drift_ledger) == 2 or (isinstance(drift_ledger, dict) and len(drift_ledger["events"]) == 2)

    # D. Variance debt timer assertions
    assert store.exists("memory/debt-timers.json")
    debt_timers = store.read_json("memory/debt-timers.json")
    assert len(debt_timers) == 1 or (isinstance(debt_timers, dict) and len(debt_timers["timers"]) == 1)

    # E. Dashboard assertions
    assert store.exists("memory/dashboard.html")
    dashboard_content = store.read("memory/dashboard.html")
    # Verify the Operator Overview section is present in dashboard
    assert "What Changed" in dashboard_content
    assert "What is Drifting" in dashboard_content
    assert "What is Overdue" in dashboard_content
    assert "Active Principles" in dashboard_content
    # And contains details of Bob's PR
    assert "PR #99" in dashboard_content
