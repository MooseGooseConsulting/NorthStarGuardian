"""Tests for guardian/analyze.py.

All LLM calls are mocked — no network access required.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from guardian.analyze import (
    LLMOutputError,
    _parse_variance_tags,
    analyze_diff,
    assess_intent,
    detect_anti_patterns,
    evaluate_alignment,
    run_interview,
)
from guardian.models import (
    AntiPattern,
    NorthStar,
    Principle,
    Verdict,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SIMPLE_DIFF = """\
--- a/guardian/analyze.py
+++ b/guardian/analyze.py
@@ -1,3 +1,5 @@
 # existing line
-old_code()
+new_code()
+import os
+from pathlib import Path
 # end
"""

DEPENDENCY_DIFF = """\
--- a/pyproject.toml
+++ b/pyproject.toml
@@ -10,2 +10,4 @@
 pydantic>=2.7.0
+requests>=2.31.0
+httpx>=0.27.0
 # end
"""

MULTI_FILE_DIFF = """\
--- a/src/main.py
+++ b/src/main.py
@@ -1,2 +1,3 @@
 x = 1
+import whisper
 y = 2
--- a/src/util.py
+++ b/src/util.py
@@ -0,0 +1,2 @@
+from datetime import datetime
+print("hello")
"""

JS_DIFF = """\
--- a/index.js
+++ b/index.js
@@ -1,2 +1,4 @@
 const x = 1;
+import React from 'react';
+const y = require('lodash');
 const z = 3;
"""


@pytest.fixture()
def simple_pr_meta() -> dict[str, Any]:
    return {
        "number": 42,
        "title": "Add new analysis module",
        "body": "Implements the core analysis pipeline.",
        "author": "alice",
        "base_sha": "abc123",
        "head_sha": "def456",
        "commit_messages": ["Add analysis module", "Fix typo"],
    }


@pytest.fixture()
def north_star() -> NorthStar:
    return NorthStar(
        version=1,
        project_name="TestProject",
        identity_statement="This is an LLM-powered analysis pipeline. It is not a collection of standalone scripts.",
        principles=[
            Principle(
                id="P1",
                rank=1,
                text="All data analysis must flow through the LLM reasoning layer",
                rationale="Core architecture constraint",
            ),
            Principle(
                id="P2",
                rank=2,
                text="Prefer the coldvox speech engine over raw whisper bindings",
            ),
            Principle(
                id="P3",
                rank=3,
                text="Zero external SaaS dependencies — local-first always",
            ),
        ],
        approved_architecture="Use AnalysisAgent for all analysis. Use ColdVox for speech.",
        anti_patterns=[
            AntiPattern(
                id="AP1",
                description="Raw script that does analysis without the LLM layer",
                example="A Python script that reads CSV and prints stats directly",
                detect="standalone.*script|print.*csv",
            ),
            AntiPattern(
                id="AP2",
                description="Direct whisper import bypassing ColdVox",
                example="import whisper",
                detect="import whisper",
            ),
        ],
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


def _make_client(response_text: str) -> MagicMock:
    """Return a mock OpenAI client whose chat.completions.create returns *response_text*."""
    client = MagicMock()
    content_block = MagicMock()
    content_block.content = response_text
    choice = MagicMock()
    choice.message = content_block
    response = MagicMock()
    response.choices = [choice]
    client.chat.completions.create.return_value = response
    return client


# ---------------------------------------------------------------------------
# analyze_diff — pure, no LLM
# ---------------------------------------------------------------------------


class TestAnalyzeDiff:
    def test_basic_file_stats(self, simple_pr_meta: dict[str, Any]) -> None:
        result = analyze_diff(SIMPLE_DIFF, simple_pr_meta)
        assert result.pr_number == 42
        assert result.pr_title == "Add new analysis module"
        assert len(result.files) == 1
        fc = result.files[0]
        assert fc.path == "guardian/analyze.py"
        assert fc.additions == 3
        assert fc.deletions == 1
        assert fc.status == "modified"

    def test_python_imports_detected(self, simple_pr_meta: dict[str, Any]) -> None:
        result = analyze_diff(SIMPLE_DIFF, simple_pr_meta)
        fc = result.files[0]
        # "import os" and "from pathlib import Path" should be detected
        assert any("import os" in imp for imp in fc.new_imports)
        assert any("pathlib" in imp for imp in fc.new_imports)

    def test_removed_imports_detected(self, simple_pr_meta: dict[str, Any]) -> None:
        diff_with_removed = """\
--- a/foo.py
+++ b/foo.py
@@ -1,2 +1,2 @@
-import os
 x = 1
+y = 2
"""
        result = analyze_diff(diff_with_removed, simple_pr_meta)
        fc = result.files[0]
        assert any("import os" in imp for imp in fc.removed_imports)

    def test_js_imports_detected(self, simple_pr_meta: dict[str, Any]) -> None:
        result = analyze_diff(JS_DIFF, simple_pr_meta)
        fc = result.files[0]
        assert any("react" in imp.lower() or "React" in imp for imp in fc.new_imports)

    def test_dependency_file_new_packages(self, simple_pr_meta: dict[str, Any]) -> None:
        result = analyze_diff(DEPENDENCY_DIFF, simple_pr_meta)
        assert "requests" in result.new_packages
        assert "httpx" in result.new_packages

    def test_multi_file_diff(self, simple_pr_meta: dict[str, Any]) -> None:
        result = analyze_diff(MULTI_FILE_DIFF, simple_pr_meta)
        assert len(result.files) == 2
        paths = {fc.path for fc in result.files}
        assert "src/main.py" in paths
        assert "src/util.py" in paths

    def test_summary_stats(self, simple_pr_meta: dict[str, Any]) -> None:
        result = analyze_diff(MULTI_FILE_DIFF, simple_pr_meta)
        assert result.summary_stats["files_changed"] == 2
        assert result.summary_stats["total_additions"] > 0

    def test_added_file_status(self, simple_pr_meta: dict[str, Any]) -> None:
        diff = """\
--- /dev/null
+++ b/newfile.py
@@ -0,0 +1,2 @@
+x = 1
+y = 2
"""
        result = analyze_diff(diff, simple_pr_meta)
        assert result.files[0].status == "added"

    def test_removed_file_status(self, simple_pr_meta: dict[str, Any]) -> None:
        diff = """\
--- a/oldfile.py
+++ /dev/null
@@ -1,2 +0,0 @@
-x = 1
-y = 2
"""
        result = analyze_diff(diff, simple_pr_meta)
        assert result.files[0].status == "removed"

    def test_pr_meta_defaults(self) -> None:
        result = analyze_diff(SIMPLE_DIFF, {})
        assert result.pr_number == 0
        assert result.pr_title == ""
        assert result.author == ""

    def test_no_llm_called(self, simple_pr_meta: dict[str, Any]) -> None:
        """analyze_diff must be pure — no anthropic calls."""
        with patch("guardian.analyze._call_llm") as mock_llm:
            analyze_diff(SIMPLE_DIFF, simple_pr_meta)
            mock_llm.assert_not_called()


# ---------------------------------------------------------------------------
# _parse_variance_tags — pure
# ---------------------------------------------------------------------------


class TestParseVarianceTags:
    def test_basic_variance(self) -> None:
        text = "Fix: [VARIANCE: P3 — hotfix for production outage, will revert within 7 days]"
        tags = _parse_variance_tags(text)
        assert len(tags) == 1
        tag = tags[0]
        assert tag.principle_id == "P3"
        assert tag.expires_in_days == 7
        assert "hotfix" in tag.justification

    def test_multiple_variances(self) -> None:
        text = (
            "[VARIANCE: P1 — experiment, 3 days] and [VARIANCE: P2 — legacy path needed, 14 days]"
        )
        tags = _parse_variance_tags(text)
        assert len(tags) == 2
        pids = {t.principle_id for t in tags}
        assert "P1" in pids
        assert "P2" in pids

    def test_default_days_when_missing(self) -> None:
        text = "[VARIANCE: P1 — no expiry mentioned here]"
        tags = _parse_variance_tags(text)
        assert tags[0].expires_in_days == 7  # default

    def test_no_variance_tags(self) -> None:
        tags = _parse_variance_tags("This PR has no variance tags.")
        assert tags == []

    def test_raw_preserved(self) -> None:
        text = "[VARIANCE: P2 — test, 5 days]"
        tags = _parse_variance_tags(text)
        assert tags[0].raw == text


# ---------------------------------------------------------------------------
# evaluate_alignment — mocked LLM
# ---------------------------------------------------------------------------


class TestEvaluateAlignment:
    def _valid_response(self, north_star: NorthStar) -> str:
        return json.dumps(
            [
                {
                    "principle_id": "P1",
                    "relevant": True,
                    "verdict": "aligned",
                    "reasoning": "The change flows through AnalysisAgent.",
                    "citations": ["src/main.py:5"],
                },
                {
                    "principle_id": "P2",
                    "relevant": False,
                    "verdict": None,
                    "reasoning": None,
                    "citations": [],
                },
                {
                    "principle_id": "P3",
                    "relevant": False,
                    "verdict": None,
                    "reasoning": None,
                    "citations": [],
                },
            ]
        )

    def test_returns_one_entry_per_principle(
        self, north_star: NorthStar, simple_pr_meta: dict[str, Any]
    ) -> None:
        diff = analyze_diff(SIMPLE_DIFF, simple_pr_meta)
        client = _make_client(self._valid_response(north_star))
        result = evaluate_alignment(diff, north_star, client=client, model="test")
        assert len(result) == 3
        ids = {e.principle_id for e in result}
        assert ids == {"P1", "P2", "P3"}

    def test_relevant_principle_has_verdict(
        self, north_star: NorthStar, simple_pr_meta: dict[str, Any]
    ) -> None:
        diff = analyze_diff(SIMPLE_DIFF, simple_pr_meta)
        client = _make_client(self._valid_response(north_star))
        result = evaluate_alignment(diff, north_star, client=client, model="test")
        p1 = next(e for e in result if e.principle_id == "P1")
        assert p1.relevant is True
        assert p1.verdict == Verdict.ALIGNED
        assert p1.reasoning is not None
        assert p1.citations == ["src/main.py:5"]

    def test_irrelevant_principle_has_no_verdict(
        self, north_star: NorthStar, simple_pr_meta: dict[str, Any]
    ) -> None:
        diff = analyze_diff(SIMPLE_DIFF, simple_pr_meta)
        client = _make_client(self._valid_response(north_star))
        result = evaluate_alignment(diff, north_star, client=client, model="test")
        p2 = next(e for e in result if e.principle_id == "P2")
        assert p2.relevant is False
        assert p2.verdict is None
        assert p2.reasoning is None

    def test_prompt_contains_north_star_text(
        self, north_star: NorthStar, simple_pr_meta: dict[str, Any]
    ) -> None:
        diff = analyze_diff(SIMPLE_DIFF, simple_pr_meta)
        client = _make_client(self._valid_response(north_star))
        evaluate_alignment(diff, north_star, client=client, model="test")
        call_args = client.chat.completions.create.call_args
        # The North Star identity statement must appear in the prompt
        prompt_text = str(call_args)
        assert north_star.identity_statement in prompt_text

    def test_malformed_json_raises_llm_output_error(
        self, north_star: NorthStar, simple_pr_meta: dict[str, Any]
    ) -> None:
        diff = analyze_diff(SIMPLE_DIFF, simple_pr_meta)
        client = _make_client("not json at all {{{")
        with pytest.raises(LLMOutputError):
            evaluate_alignment(diff, north_star, client=client, model="test")

    def test_wrong_type_raises_llm_output_error(
        self, north_star: NorthStar, simple_pr_meta: dict[str, Any]
    ) -> None:
        diff = analyze_diff(SIMPLE_DIFF, simple_pr_meta)
        client = _make_client('{"wrong": "type"}')
        with pytest.raises(LLMOutputError):
            evaluate_alignment(diff, north_star, client=client, model="test")

    def test_invalid_verdict_raises_llm_output_error(
        self, north_star: NorthStar, simple_pr_meta: dict[str, Any]
    ) -> None:
        diff = analyze_diff(SIMPLE_DIFF, simple_pr_meta)
        bad_response = json.dumps(
            [
                {
                    "principle_id": "P1",
                    "relevant": True,
                    "verdict": "UNKNOWN_VALUE",
                    "reasoning": "...",
                    "citations": [],
                },
                {
                    "principle_id": "P2",
                    "relevant": False,
                    "verdict": None,
                    "reasoning": None,
                    "citations": [],
                },
                {
                    "principle_id": "P3",
                    "relevant": False,
                    "verdict": None,
                    "reasoning": None,
                    "citations": [],
                },
            ]
        )
        client = _make_client(bad_response)
        with pytest.raises(LLMOutputError):
            evaluate_alignment(diff, north_star, client=client, model="test")

    def test_missing_principle_gets_default_entry(
        self, north_star: NorthStar, simple_pr_meta: dict[str, Any]
    ) -> None:
        """If the LLM omits a principle, evaluate_alignment fills in a default."""
        diff = analyze_diff(SIMPLE_DIFF, simple_pr_meta)
        # Only P1 returned — P2 and P3 are missing
        partial = json.dumps(
            [
                {
                    "principle_id": "P1",
                    "relevant": True,
                    "verdict": "aligned",
                    "reasoning": "ok",
                    "citations": [],
                },
            ]
        )
        client = _make_client(partial)
        result = evaluate_alignment(diff, north_star, client=client, model="test")
        assert len(result) == 3
        missing = [e for e in result if e.principle_id in {"P2", "P3"}]
        for e in missing:
            assert e.relevant is False

    def test_json_inside_markdown_fence_accepted(
        self, north_star: NorthStar, simple_pr_meta: dict[str, Any]
    ) -> None:
        diff = analyze_diff(SIMPLE_DIFF, simple_pr_meta)
        fenced = "```json\n" + self._valid_response(north_star) + "\n```"
        client = _make_client(fenced)
        result = evaluate_alignment(diff, north_star, client=client, model="test")
        assert len(result) == 3

    def test_drift_verdict_parsed(
        self, north_star: NorthStar, simple_pr_meta: dict[str, Any]
    ) -> None:
        diff = analyze_diff(SIMPLE_DIFF, simple_pr_meta)
        response = json.dumps(
            [
                {
                    "principle_id": "P1",
                    "relevant": True,
                    "verdict": "drift",
                    "reasoning": "Bypasses LLM layer.",
                    "citations": ["src/main.py:3"],
                },
                {
                    "principle_id": "P2",
                    "relevant": False,
                    "verdict": None,
                    "reasoning": None,
                    "citations": [],
                },
                {
                    "principle_id": "P3",
                    "relevant": False,
                    "verdict": None,
                    "reasoning": None,
                    "citations": [],
                },
            ]
        )
        client = _make_client(response)
        result = evaluate_alignment(diff, north_star, client=client, model="test")
        p1 = next(e for e in result if e.principle_id == "P1")
        assert p1.verdict == Verdict.DRIFT


# ---------------------------------------------------------------------------
# detect_anti_patterns — mocked LLM
# ---------------------------------------------------------------------------


class TestDetectAntiPatterns:
    def test_empty_when_no_anti_patterns(self, simple_pr_meta: dict[str, Any]) -> None:
        empty_north_star = NorthStar(
            version=1,
            project_name="Empty",
            identity_statement="Nothing here.",
            principles=[],
            approved_architecture="n/a",
            anti_patterns=[],
            created_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
        diff = analyze_diff(SIMPLE_DIFF, simple_pr_meta)
        client = MagicMock()
        result = detect_anti_patterns(diff, empty_north_star, client=client, model="test")
        assert result == []
        client.chat.completions.create.assert_not_called()

    def test_returns_matches(self, north_star: NorthStar, simple_pr_meta: dict[str, Any]) -> None:
        diff = analyze_diff(MULTI_FILE_DIFF, simple_pr_meta)
        response = json.dumps(
            [
                {
                    "pattern_id": "AP2",
                    "location": "src/main.py:2",
                    "explanation": "Direct whisper import detected.",
                },
            ]
        )
        client = _make_client(response)
        result = detect_anti_patterns(diff, north_star, client=client, model="test")
        assert len(result) == 1
        assert result[0].pattern_id == "AP2"
        assert result[0].location == "src/main.py:2"

    def test_empty_array_response_ok(
        self, north_star: NorthStar, simple_pr_meta: dict[str, Any]
    ) -> None:
        diff = analyze_diff(SIMPLE_DIFF, simple_pr_meta)
        client = _make_client("[]")
        result = detect_anti_patterns(diff, north_star, client=client, model="test")
        assert result == []

    def test_prompt_contains_anti_pattern_descriptions(
        self, north_star: NorthStar, simple_pr_meta: dict[str, Any]
    ) -> None:
        diff = analyze_diff(SIMPLE_DIFF, simple_pr_meta)
        client = _make_client("[]")
        detect_anti_patterns(diff, north_star, client=client, model="test")
        call_args = client.chat.completions.create.call_args
        prompt_text = str(call_args)
        assert "AP1" in prompt_text
        assert "AP2" in prompt_text

    def test_malformed_json_raises_llm_output_error(
        self, north_star: NorthStar, simple_pr_meta: dict[str, Any]
    ) -> None:
        diff = analyze_diff(SIMPLE_DIFF, simple_pr_meta)
        client = _make_client("this is not json")
        with pytest.raises(LLMOutputError):
            detect_anti_patterns(diff, north_star, client=client, model="test")

    def test_invented_pattern_id_skipped(
        self, north_star: NorthStar, simple_pr_meta: dict[str, Any]
    ) -> None:
        diff = analyze_diff(SIMPLE_DIFF, simple_pr_meta)
        response = json.dumps(
            [
                {"pattern_id": "AP_INVENTED", "location": "foo.py:1", "explanation": "Made up."},
            ]
        )
        client = _make_client(response)
        result = detect_anti_patterns(diff, north_star, client=client, model="test")
        assert result == []


# ---------------------------------------------------------------------------
# assess_intent — mocked LLM
# ---------------------------------------------------------------------------


class TestAssessIntent:
    def _valid_response(self) -> str:
        return json.dumps(
            {
                "one_line": "Added a new analysis pipeline step.",
                "paragraph": (
                    "The developer added a new analysis step that routes through "
                    "AnalysisAgent. This extends the existing analysis infrastructure "
                    "and aligns with the declared architecture. The change is self-contained."
                ),
            }
        )

    def test_returns_intent_summary(self, simple_pr_meta: dict[str, Any]) -> None:
        diff = analyze_diff(SIMPLE_DIFF, simple_pr_meta)
        client = _make_client(self._valid_response())
        result = assess_intent(simple_pr_meta, diff, client=client, model="test")
        assert result.one_line == "Added a new analysis pipeline step."
        assert len(result.paragraph) > 0

    def test_variance_tags_parsed(self, simple_pr_meta: dict[str, Any]) -> None:
        meta_with_variance = {
            **simple_pr_meta,
            "body": "Fix. [VARIANCE: P3 — hotfix, will revert within 5 days]",
        }
        diff = analyze_diff(SIMPLE_DIFF, meta_with_variance)
        client = _make_client(self._valid_response())
        result = assess_intent(meta_with_variance, diff, client=client, model="test")
        assert len(result.declared_variances) == 1
        assert result.declared_variances[0].principle_id == "P3"
        assert result.declared_variances[0].expires_in_days == 5

    def test_no_variance_tags_gives_empty_list(self, simple_pr_meta: dict[str, Any]) -> None:
        diff = analyze_diff(SIMPLE_DIFF, simple_pr_meta)
        client = _make_client(self._valid_response())
        result = assess_intent(simple_pr_meta, diff, client=client, model="test")
        assert result.declared_variances == []

    def test_malformed_json_raises_llm_output_error(self, simple_pr_meta: dict[str, Any]) -> None:
        diff = analyze_diff(SIMPLE_DIFF, simple_pr_meta)
        client = _make_client("{{broken")
        with pytest.raises(LLMOutputError):
            assess_intent(simple_pr_meta, diff, client=client, model="test")

    def test_wrong_type_raises_llm_output_error(self, simple_pr_meta: dict[str, Any]) -> None:
        diff = analyze_diff(SIMPLE_DIFF, simple_pr_meta)
        client = _make_client("[1, 2, 3]")
        with pytest.raises(LLMOutputError):
            assess_intent(simple_pr_meta, diff, client=client, model="test")

    def test_empty_one_line_raises_llm_output_error(self, simple_pr_meta: dict[str, Any]) -> None:
        diff = analyze_diff(SIMPLE_DIFF, simple_pr_meta)
        client = _make_client(json.dumps({"one_line": "", "paragraph": "Some text."}))
        with pytest.raises(LLMOutputError):
            assess_intent(simple_pr_meta, diff, client=client, model="test")


# ---------------------------------------------------------------------------
# run_interview — integration with mocked LLM
# ---------------------------------------------------------------------------


class TestRunInterview:
    def _make_all_mock_client(self, north_star: NorthStar) -> MagicMock:
        """Return a client that returns sensible JSON for each call in order."""
        intent_resp = json.dumps(
            {
                "one_line": "Added analysis step.",
                "paragraph": "Developer added a step through AnalysisAgent.",
            }
        )
        alignment_resp = json.dumps(
            [
                {
                    "principle_id": "P1",
                    "relevant": True,
                    "verdict": "aligned",
                    "reasoning": "Routes through AnalysisAgent.",
                    "citations": [],
                },
                {
                    "principle_id": "P2",
                    "relevant": False,
                    "verdict": None,
                    "reasoning": None,
                    "citations": [],
                },
                {
                    "principle_id": "P3",
                    "relevant": False,
                    "verdict": None,
                    "reasoning": None,
                    "citations": [],
                },
            ]
        )
        ap_resp = "[]"
        chronicle_resp = (
            "PR #42 added a new analysis step. It aligns with the declared architecture."
        )

        client = MagicMock()
        responses = []
        for text in [intent_resp, alignment_resp, ap_resp, chronicle_resp]:
            block = MagicMock()
            block.content = text
            choice = MagicMock()
            choice.message = block
            resp = MagicMock()
            resp.choices = [choice]
            responses.append(resp)

        client.chat.completions.create.side_effect = responses
        return client

    def test_run_interview_returns_report(
        self, north_star: NorthStar, simple_pr_meta: dict[str, Any]
    ) -> None:
        diff = analyze_diff(SIMPLE_DIFF, simple_pr_meta)
        client = self._make_all_mock_client(north_star)
        report = run_interview(
            diff, north_star, client=client, model="test", pr_meta=simple_pr_meta
        )
        assert report.pr_number == 42
        assert report.overall_verdict == Verdict.ALIGNED
        assert report.alignment_summary == "Added analysis step."
        assert len(report.principle_evaluations) == 3
        assert report.chronicle_paragraph != ""

    def test_run_interview_drift_aggregate(
        self, north_star: NorthStar, simple_pr_meta: dict[str, Any]
    ) -> None:
        diff = analyze_diff(SIMPLE_DIFF, simple_pr_meta)
        intent_resp = json.dumps(
            {
                "one_line": "Added whisper directly.",
                "paragraph": "Developer imported whisper without going through ColdVox.",
            }
        )
        alignment_resp = json.dumps(
            [
                {
                    "principle_id": "P1",
                    "relevant": True,
                    "verdict": "aligned",
                    "reasoning": "ok",
                    "citations": [],
                },
                {
                    "principle_id": "P2",
                    "relevant": True,
                    "verdict": "drift",
                    "reasoning": "Bypasses ColdVox.",
                    "citations": ["src/main.py:2"],
                },
                {
                    "principle_id": "P3",
                    "relevant": False,
                    "verdict": None,
                    "reasoning": None,
                    "citations": [],
                },
            ]
        )
        ap_resp = "[]"
        chronicle_resp = "Drift detected."

        client = MagicMock()
        responses = []
        for text in [intent_resp, alignment_resp, ap_resp, chronicle_resp]:
            block = MagicMock()
            block.content = text
            choice = MagicMock()
            choice.message = block
            resp = MagicMock()
            resp.choices = [choice]
            responses.append(resp)

        client.chat.completions.create.side_effect = responses
        report = run_interview(diff, north_star, client=client, model="test")
        assert report.overall_verdict == Verdict.DRIFT

    def test_run_interview_ambiguous_aggregate(
        self, north_star: NorthStar, simple_pr_meta: dict[str, Any]
    ) -> None:
        diff = analyze_diff(SIMPLE_DIFF, simple_pr_meta)
        intent_resp = json.dumps({"one_line": "Cache layer added.", "paragraph": "..."})
        alignment_resp = json.dumps(
            [
                {
                    "principle_id": "P1",
                    "relevant": False,
                    "verdict": None,
                    "reasoning": None,
                    "citations": [],
                },
                {
                    "principle_id": "P2",
                    "relevant": False,
                    "verdict": None,
                    "reasoning": None,
                    "citations": [],
                },
                {
                    "principle_id": "P3",
                    "relevant": True,
                    "verdict": "ambiguous",
                    "reasoning": "Could be local or remote.",
                    "citations": [],
                },
            ]
        )
        ap_resp = "[]"
        chronicle_resp = "Ambiguous caching layer added."

        responses = []
        for text in [intent_resp, alignment_resp, ap_resp, chronicle_resp]:
            block = MagicMock()
            block.content = text
            choice = MagicMock()
            choice.message = block
            resp = MagicMock()
            resp.choices = [choice]
            responses.append(resp)

        client = MagicMock()
        client.chat.completions.create.side_effect = responses
        report = run_interview(diff, north_star, client=client, model="test")
        assert report.overall_verdict == Verdict.AMBIGUOUS

    def test_run_interview_makes_four_llm_calls(
        self, north_star: NorthStar, simple_pr_meta: dict[str, Any]
    ) -> None:
        diff = analyze_diff(SIMPLE_DIFF, simple_pr_meta)
        client = self._make_all_mock_client(north_star)
        run_interview(diff, north_star, client=client, model="test", pr_meta=simple_pr_meta)
        # 1 assess_intent + 1 evaluate_alignment + 1 detect_anti_patterns + 1 chronicle
        assert client.chat.completions.create.call_count == 4
