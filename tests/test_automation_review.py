from dataclasses import replace
import json
from pathlib import Path
import subprocess
import os

import pytest

from kalshi_stats.automation_pipeline import run_review_pipeline
from kalshi_stats.automation_review import (
    FindingSeverity, ReviewFinding, ReviewResult, ReviewerVerdict,
    parse_reviewer_output, reviewer_prompt,
)
from kalshi_stats.automation_state import ErrorClassification, RunRecord, TaskRecord, TaskStatus
from kalshi_stats.automation_validation import GateResult, ValidationReport


NOW = "2026-08-31T12:00:00Z"
GIT_ENV = {key: value for key, value in os.environ.items() if key not in {"GIT_DIR", "GIT_WORK_TREE"}}


@pytest.fixture
def records(tmp_path):
    root = tmp_path / "task"
    root.mkdir()
    subprocess.run(("git", "init", "-b", "automation/base", str(root)), check=True, capture_output=True, env=GIT_ENV)
    subprocess.run(("git", "-C", str(root), "config", "user.name", "test"), check=True, env=GIT_ENV)
    subprocess.run(("git", "-C", str(root), "config", "user.email", "test@invalid"), check=True, env=GIT_ENV)
    (root / "README.md").write_text("base\n")
    subprocess.run(("git", "-C", str(root), "add", "."), check=True, env=GIT_ENV)
    subprocess.run(("git", "-C", str(root), "commit", "-m", "base"), check=True, capture_output=True, env=GIT_ENV)
    task = TaskRecord.create(task_id="review", title="review", objective="bounded spec", branch="automation/review", worktree=str(root), base_branch="automation/base", now=NOW)
    run = RunRecord("builder-0", task.task_id, TaskStatus.VALIDATING, "builder-session-0", NOW, NOW, task.branch, task.worktree, (), {}, "final", "events", ErrorClassification.SUCCESS, "validate")
    return root, task, run


def report(passed=True, classification=None):
    gate = GateResult("gate", passed, classification=classification)
    return ValidationReport(NOW, NOW, passed, (), (gate,))


def review(builder, cycle, verdict=ReviewerVerdict.PASS):
    findings = () if verdict is ReviewerVerdict.PASS else (ReviewFinding(FindingSeverity.MEDIUM, "FIX", "fix", "fix it"),)
    return ReviewResult(verdict, findings, f"review-{cycle}", f"reviewer-session-{cycle}", builder.run_id, builder.session_thread_id, cycle)


def test_failed_gate_prevents_reviewer_pass(records, tmp_path):
    _, task, builder = records
    calls = []
    result = run_review_pipeline(task, builder, validate=lambda *_: report(False), review=lambda *_: calls.append(1), repair=lambda *_: None, evidence_directory=tmp_path)
    assert result.status == "FAILED"
    assert not calls


def test_reviewer_must_be_distinct_and_structured(records):
    _, task, builder = records
    with pytest.raises(ValueError, match="distinct"):
        ReviewResult(ReviewerVerdict.PASS, (), "r", builder.session_thread_id, builder.run_id, builder.session_thread_id, 0)
    parsed = parse_reviewer_output('{"verdict":"PASS","findings":[]}', reviewer_run_id="r", reviewer_session_id="reviewer", builder_run_id=builder.run_id, builder_session_id=builder.session_thread_id, repair_cycle=0)
    assert parsed.verdict is ReviewerVerdict.PASS
    prompt = reviewer_prompt(task, builder_run_id=builder.run_id, validation_path="validation.json", diff_text="diff")
    assert task.objective in prompt and "validation.json" in prompt and "diff" in prompt


def test_pass_persists_separate_reviewer_evidence(records, tmp_path):
    _, task, builder = records
    result = run_review_pipeline(task, builder, validate=lambda *_: report(), review=lambda t, b, v, c: review(b, c), repair=lambda *_: None, evidence_directory=tmp_path)
    assert result.status == "PASSED"
    payload = json.loads((tmp_path / "review-0.json").read_text())
    assert payload["builder_session_id"] != payload["reviewer_session_id"]


def test_changes_required_uses_fresh_builder_and_revalidates(records, tmp_path):
    _, task, builder = records
    validations = []
    def validate(task, run, cycle):
        validations.append(run.run_id)
        return report()
    def reviewer(task, run, validation, cycle):
        return review(run, cycle, ReviewerVerdict.CHANGES_REQUIRED if cycle == 0 else ReviewerVerdict.PASS)
    repaired = replace(builder, run_id="builder-1", session_thread_id="builder-session-1")
    result = run_review_pipeline(task, builder, validate=validate, review=reviewer, repair=lambda *_: (repaired, ErrorClassification.SUCCESS), evidence_directory=tmp_path)
    assert result.status == "PASSED"
    assert validations == ["builder-0", "builder-1"]
    assert result.repair_cycles == 1


def test_max_repairs_and_nonrepairable_fail_closed(records, tmp_path):
    _, task, builder = records
    counter = [0]
    def repair(*_):
        counter[0] += 1
        return replace(builder, run_id=f"builder-{counter[0]}", session_thread_id=f"builder-session-{counter[0]}"), ErrorClassification.SUCCESS
    result = run_review_pipeline(task, builder, validate=lambda *_: report(), review=lambda t, b, v, c: review(b, c, ReviewerVerdict.CHANGES_REQUIRED), repair=repair, evidence_directory=tmp_path, max_repair_cycles=2)
    assert result.status == "FAILED" and counter[0] == 2
    for classification in ("SECURITY_VIOLATION", "DATABASE_INTEGRITY_FAILURE"):
        result = run_review_pipeline(task, builder, validate=lambda *_: report(False, classification), review=lambda *_: None, repair=lambda *_: None, evidence_directory=tmp_path)
        assert result.status == "BLOCKED" and result.classification.value == classification


def test_reviewer_worktree_modification_is_detected(records, tmp_path):
    root, task, builder = records
    def reviewer(task, run, validation, cycle):
        (root / "README.md").write_text("reviewer edit\n")
        return review(run, cycle)
    result = run_review_pipeline(task, builder, validate=lambda *_: report(), review=reviewer, repair=lambda *_: None, evidence_directory=tmp_path)
    assert result.status == "BLOCKED"
    assert result.classification is ErrorClassification.SECURITY_VIOLATION


def test_secret_text_is_redacted_from_findings(records):
    _, _, builder = records
    openai_shaped = "sk-" + "exampleSecret123"
    bearer_shaped = "Bearer " + "abc.def.ghi"
    payload = json.dumps({"verdict": "CHANGES_REQUIRED", "findings": [{"severity": "HIGH", "code": "S", "summary": openai_shaped, "detail": bearer_shaped}]})
    parsed = parse_reviewer_output(payload, reviewer_run_id="r", reviewer_session_id="reviewer", builder_run_id=builder.run_id, builder_session_id=builder.session_thread_id, repair_cycle=0)
    assert "exampleSecret" not in json.dumps(parsed.to_dict())

def test_coordinator_task_state_change_during_review_is_allowed(records, tmp_path):
    root, task, builder = records

    def reviewer(task, run, validation, cycle):
        state = root / "automation" / "tasks" / f"{task.task_id}.json"
        state.parent.mkdir(parents=True, exist_ok=True)
        state.write_text('{"coordinator":"reviewing"}\n')
        return review(run, cycle)

    result = run_review_pipeline(
        task,
        builder,
        validate=lambda *_: report(),
        review=reviewer,
        repair=lambda *_: None,
        evidence_directory=tmp_path,
    )

    assert result.status == "PASSED"
    assert result.classification is ErrorClassification.SUCCESS
