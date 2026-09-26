import json
from unittest.mock import patch

from driver_port_factory.codex.cli import run_codex_stage
from driver_port_factory.codex.context_focus import compact_feedback, reading_plan, repair_focus
from driver_port_factory.codex.contracts import CodexBackend
from driver_port_factory.codex.gateway import CodexResult
from driver_port_factory.codex.sessions import input_changes
from driver_port_factory.core.events import StageEvent
from driver_port_factory.migration.contracts import MigrationArtifact
from driver_port_factory.migration.contracts import MigrationStage as S
from tests.submission_support import submit
from tests.test_observation_handoff import observe
from tests.workflow_support import ready_implementation


def ref(kind, digest):
    return {"kind": kind, "digest": digest, "path": f"/evidence/{digest}"}


def test_reading_plan_prioritizes_changed_evidence_without_losing_other_inputs():
    context = {"inputs": [ref("migration_contracts", "contract"),
                          ref("target_study_report", "study"),
                          ref("other", "old"), ref("migration_contracts", "contract")]}
    _, known = input_changes(context, {})
    context["inputs"][2] = ref("other", "new")
    annotated, _ = input_changes(context, known)
    before = json.dumps(annotated, sort_keys=True)
    plan = reading_plan(S.DRIVER_IMPLEMENTATION, annotated)
    assert plan["first"][0] == {"location": "inputs/2", "kind": "other",
                                "input_status": "changed"}
    assert [r["kind"] for r in plan["first"]].count("migration_contracts") == 1
    assert plan["first"][1]["input_status"] == "unchanged"
    assert json.dumps(annotated, sort_keys=True) == before
    assert reading_plan(S.DRIVER_IMPLEMENTATION, {}) is None


def test_reading_plan_bounds_priority_list_but_preserves_catalog():
    context = {"inputs": [ref("migration_contracts", str(i)) for i in range(20)]}
    plan = reading_plan(S.DRIVER_IMPLEMENTATION, context)
    assert len(plan["first"]) == 8 and plan["other_unique_references"] == 12
    assert len(context["inputs"]) == 20


def test_long_feedback_is_losslessly_archived_and_short_feedback_unchanged(tmp_path):
    project = ready_implementation(tmp_path)
    text = "开始\n" + "repeated diagnostic\n" * 500 + "critical final error\n"
    context = {"controller_feedback": text, "checker_findings": "short", "other": text,
               "controller_execution": {"error": text}}
    result = compact_feedback(project, context)
    feedback = result["controller_feedback"]
    assert project.artifacts.path_for_digest(feedback["full_record"]["digest"]).read_text() == text
    assert "critical final error" in feedback["excerpt"]
    assert len(json.dumps(feedback)) < len(text)
    assert result["checker_findings"] == "short" and result["other"] == text
    assert result["controller_execution"] == context["controller_execution"]
    assert compact_feedback(project, context) == result
    assert context["controller_feedback"] == text


def test_repair_focus_retains_transition_but_never_crosses_reopened_episode(tmp_path):
    project = ready_implementation(tmp_path)
    observe(project, 1, {"runtime_bound": False, "logs_observed": False})
    observe(project, 2, {"runtime_bound": True, "logs_observed": False})
    observe(project, 3, {"runtime_bound": True, "logs_observed": False})
    result = repair_focus(project, S.DRIVER_IMPLEMENTATION,
                          {"input_changes": {"changed": ["inputs/runtime"]}})
    assert result["changed_observations"] == []
    assert result["last_transition"]["changes"] == [
        {"field": "runtime_bound", "previous": False, "current": True}]
    assert result["latest_observation"]["observation"]["logs_observed"] is False
    assert result["changed_input_locations"] == ["inputs/runtime"]
    project.record_event(StageEvent.RETRIED, {
        "stage": S.DRIVER_IMPLEMENTATION.value, "operator_reopen": True})
    result = repair_focus(project, S.DRIVER_IMPLEMENTATION, {})
    assert result is None


def test_worker_receives_navigation_and_complete_feedback_reference(tmp_path):
    project = ready_implementation(tmp_path)
    feedback = "synthetic failure\n" * 600
    contract = project.artifact(S.CONTRACTS, MigrationArtifact.CONTRACTS)
    context = {"frozen_inputs": {"contract": {
        "kind": contract.kind, "digest": contract.digest,
        "path": str(project.artifacts.path_for_digest(contract.digest))}}}

    def gateway(job):
        header = json.loads(job.prompt.split("<job>", 1)[1].split("</job>", 1)[0])
        material = header["reference_material"]
        record = material["controller_feedback"]["full_record"]
        assert project.artifacts.path_for_digest(record["digest"]).read_text() == feedback
        assert material["reading_plan"]["first"][0]["kind"] == "migration_contracts"
        assert "repair_focus" not in material  # No history or changed inputs to summarize.
        report = job.execution_root / ".dpf-output/focus-report.md"
        report.parent.mkdir(exist_ok=True)
        report.write_text("Synthetic blocked result, not a functioning driver.")
        submit(project, job, report, kind="report", decision="blocked")
        return CodexResult(job.job_id, "", "focus-worker")

    with patch("driver_port_factory.codex.cli.CodexExecGateway.run", side_effect=gateway):
        run_codex_stage(project, S.DRIVER_IMPLEMENTATION, context=context,
                        follow_up=feedback, backend=CodexBackend.EXEC,
                        codex_bin="codex", model=None)
    project.verify_integrity()
