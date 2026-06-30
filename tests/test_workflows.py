"""Structural validation for the three GitHub Actions workflow files."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

WORKFLOWS_DIR = Path(__file__).parent.parent / ".github" / "workflows"


def _load(filename: str) -> dict[str, Any]:
    """Parse a workflow YAML file and return the top-level mapping."""
    path = WORKFLOWS_DIR / filename
    assert path.exists(), f"Workflow file not found: {path}"
    content = path.read_text(encoding="utf-8")
    data = yaml.safe_load(content)
    assert isinstance(data, dict), f"{filename}: expected top-level mapping, got {type(data)}"
    return data


def _get_trigger(data: dict[str, Any]) -> Any:
    # PyYAML 1.1 parses bare `on:` as the boolean True; accept either form.
    if "on" in data:
        return data["on"]
    if True in data:
        return data[True]
    return None


def _steps_run_strings(job: dict[str, Any]) -> list[str]:
    """Collect every `run:` string from the steps of a job."""
    runs: list[str] = []
    for step in job.get("steps", []):
        if isinstance(step, dict) and "run" in step:
            runs.append(str(step["run"]))
    return runs


# ---------------------------------------------------------------------------
# Parametrised parse test — all three files must parse without raising
# ---------------------------------------------------------------------------

WORKFLOW_FILES = ["guardian.yml", "guardian-debt.yml", "ci.yml"]


@pytest.mark.parametrize("filename", WORKFLOW_FILES)
def test_workflow_parses(filename: str) -> None:
    """Each workflow file must be valid YAML."""
    _load(filename)  # raises on parse error


# ---------------------------------------------------------------------------
# Shared structural invariants
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("filename", WORKFLOW_FILES)
def test_trigger_declared(filename: str) -> None:
    """Every workflow must have a non-empty trigger declaration."""
    data = _load(filename)
    trigger = _get_trigger(data)
    assert trigger is not None, f"{filename}: no trigger key ('on' or True) found"
    assert trigger, f"{filename}: trigger declaration is empty"


@pytest.mark.parametrize("filename", WORKFLOW_FILES)
def test_jobs_block_non_empty(filename: str) -> None:
    """Every workflow must define at least one job."""
    data = _load(filename)
    jobs = data.get("jobs")
    assert isinstance(jobs, dict) and len(jobs) >= 1, (
        f"{filename}: 'jobs' block is missing or empty"
    )


@pytest.mark.parametrize("filename", WORKFLOW_FILES)
def test_each_job_has_runs_on_and_permissions(filename: str) -> None:
    """Every job must have runs-on, and permissions must exist at job or workflow level."""
    data = _load(filename)
    workflow_permissions = data.get("permissions")
    jobs: dict[str, Any] = data.get("jobs", {})

    for job_name, job in jobs.items():
        assert isinstance(job, dict), f"{filename}/{job_name}: job is not a mapping"
        assert "runs-on" in job, f"{filename}/{job_name}: missing 'runs-on'"

        has_permissions = (
            "permissions" in job  # job-level
            or workflow_permissions is not None  # workflow-level
        )
        assert has_permissions, (
            f"{filename}/{job_name}: no permissions block at job or workflow level"
        )


# ---------------------------------------------------------------------------
# guardian.yml — specific assertions
# ---------------------------------------------------------------------------


class TestGuardianWorkflow:
    FILENAME = "guardian.yml"

    def _data(self) -> dict[str, Any]:
        return _load(self.FILENAME)

    def test_has_interview_job(self) -> None:
        jobs = self._data().get("jobs", {})
        assert "interview" in jobs, "guardian.yml: missing 'interview' job"

    def test_has_no_command_job(self) -> None:
        jobs = self._data().get("jobs", {})
        assert "command" not in jobs, "guardian.yml must not expose slash commands"

    def test_trigger_has_pull_request(self) -> None:
        trigger = _get_trigger(self._data())
        assert isinstance(trigger, dict) and "pull_request" in trigger, (
            "guardian.yml: trigger must declare 'pull_request'"
        )

    def test_trigger_has_no_issue_comment(self) -> None:
        trigger = _get_trigger(self._data())
        assert isinstance(trigger, dict) and "issue_comment" not in trigger, (
            "guardian.yml must not react to human comment commands"
        )


# ---------------------------------------------------------------------------
# guardian-debt.yml — specific assertions
# ---------------------------------------------------------------------------


class TestGuardianDebtWorkflow:
    FILENAME = "guardian-debt.yml"

    def _data(self) -> dict[str, Any]:
        return _load(self.FILENAME)

    def test_trigger_has_schedule_or_workflow_dispatch(self) -> None:
        trigger = _get_trigger(self._data())
        assert isinstance(trigger, dict), "guardian-debt.yml: trigger must be a mapping"
        has_schedule = "schedule" in trigger
        has_dispatch = "workflow_dispatch" in trigger
        assert has_schedule or has_dispatch, (
            "guardian-debt.yml: trigger must declare 'schedule' or 'workflow_dispatch'"
        )

    def test_has_at_least_one_job(self) -> None:
        jobs = self._data().get("jobs", {})
        assert len(jobs) >= 1, "guardian-debt.yml: must have at least one job"


# ---------------------------------------------------------------------------
# ci.yml — specific assertions
# ---------------------------------------------------------------------------


class TestCIWorkflow:
    FILENAME = "ci.yml"

    def _data(self) -> dict[str, Any]:
        return _load(self.FILENAME)

    def test_trigger_has_pull_request(self) -> None:
        trigger = _get_trigger(self._data())
        assert isinstance(trigger, dict) and "pull_request" in trigger, (
            "ci.yml: trigger must declare 'pull_request'"
        )

    def test_trigger_has_push(self) -> None:
        trigger = _get_trigger(self._data())
        assert isinstance(trigger, dict) and "push" in trigger, (
            "ci.yml: trigger must declare 'push'"
        )

    def test_job_steps_run_pytest(self) -> None:
        jobs = self._data().get("jobs", {})
        assert jobs, "ci.yml: no jobs found"
        job = next(iter(jobs.values()))
        runs = _steps_run_strings(job)
        assert any(re.search(r"\bpytest\b", r) for r in runs), "ci.yml: no step runs 'pytest'"

    def test_job_steps_run_ruff(self) -> None:
        jobs = self._data().get("jobs", {})
        assert jobs, "ci.yml: no jobs found"
        job = next(iter(jobs.values()))
        runs = _steps_run_strings(job)
        assert any(re.search(r"\bruff\b", r) for r in runs), "ci.yml: no step runs 'ruff'"
