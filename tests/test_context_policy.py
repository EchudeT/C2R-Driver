"""Opt-in context experiments must preserve evidence and avoid reset loops."""

import json
from dataclasses import replace
from unittest.mock import PropertyMock, patch

import pytest

from driver_port_factory.cli import main
from driver_port_factory.codex.cli import run_codex_stage
from driver_port_factory.codex.context_policy import configure_policy, prepare_context, read_policy
from driver_port_factory.codex.contracts import CodexBackend
from driver_port_factory.codex.gateway import CodexResult
from driver_port_factory.codex.policy import CodexExecutionPolicy
from driver_port_factory.codex.sessions import read_session, save_session, stage_session
from driver_port_factory.composition import open_project
from driver_port_factory.control.context_report import compare_reports, context_report
from driver_port_factory.migration.contracts import MigrationStage as S
from tests.submission_support import submit
from tests.workflow_support import ready_implementation


def worker(project):
    grant = CodexExecutionPolicy().grant(project, S.DRIVER_IMPLEMENTATION)
    key, _ = stage_session(project, S.DRIVER_IMPLEMENTATION, grant, None, "exec")
    save_session(project, key, "existing-worker", {"knowledge-guided-driver-port/SKILL.md": "old"})
    return key, read_session(project, key)


def test_default_and_nonboundary_calls_preserve_session(tmp_path):
    project = ready_implementation(tmp_path)
    assert read_policy(project)["name"] == "analysis-handoff"
    configure_policy(project, "persistent", reason="test persistent opt-out")
    key, session = worker(project)
    assert read_policy(project)["name"] == "persistent"
    unchanged, decision = prepare_context(
        project, S.DRIVER_IMPLEMENTATION, key, session, continuing=False)
    assert unchanged == session and decision["decision"] == "preserve"
    configure_policy(project, "implementation-handoff", reason="pilot")
    for stage, continuing in [(S.ANALYSIS_REVIEW, False), (S.DRIVER_IMPLEMENTATION, True),
                              (S.ARTIFACT_PREPARATION, False)]:
        unchanged, decision = prepare_context(project, stage, key, session, continuing=continuing)
        assert unchanged == session and decision["decision"] == "preserve"


def test_boundary_rotates_once_and_retry_resumes_after_restart(tmp_path):
    project = ready_implementation(tmp_path)
    key, _ = worker(project)
    configure_policy(project, "implementation-handoff", reason="pilot")
    expected_thread = [None, "new-worker"]

    def gateway(job):
        assert job.thread_id == expected_thread.pop(0)
        if job.thread_id is None:
            assert "context_handoff" in job.prompt
            assert '<skill_document path="knowledge-guided-driver-port/SKILL.md"' in job.prompt
        report = job.execution_root / ".dpf-output/context-report.md"
        report.parent.mkdir(exist_ok=True)
        report.write_text("Synthetic result; device operation remains unverified.")
        submit(project, job, report, kind="report", decision="blocked")
        return CodexResult(job.job_id, "", "new-worker")

    with patch("driver_port_factory.codex.cli.CodexExecGateway.run", side_effect=gateway):
        for _ in range(2):
            project = open_project(project.root)
            run_codex_stage(project, S.DRIVER_IMPLEMENTATION, context={},
                            backend=CodexBackend.EXEC, codex_bin="codex", model=None)
    assert not expected_thread
    assert read_session(project, key)["automatic_boundary"] == "first_driver_implementation"
    records = context_report(project)
    assert records["totals"]["context_actions"] == {"handoff": 1, "resume": 1}
    assert len(records["by_epoch"]) == 1
    assert records["totals"]["unknown_usage_calls"] == 2
    assert records["totals"]["cost_complete"] is False
    assert len([e for e in records["history"]["context_events"]
                if e["event_type"] == "run.session_reset"]) == 1
    project.verify_integrity()


@pytest.mark.parametrize("case", ["late_opt_in", "fresh", "pending_handoff", "missing_contracts"])
def test_ineligible_boundary_is_skipped_not_blocked(tmp_path, case):
    project = ready_implementation(tmp_path, plan=case != "missing_contracts")
    key, session = worker(project)
    configure_policy(project, "implementation-handoff", reason="pilot")
    if case == "late_opt_in":
        (project.control / "codex/driver_implementation-old.metrics.json").write_text("{}")
    elif case == "fresh":
        session = {}
    elif case == "pending_handoff":
        session = {"handoff": {"digest": "existing"}}
    result, decision = prepare_context(project, S.DRIVER_IMPLEMENTATION, key, session,
                                       continuing=False)
    assert result == session and decision["decision"] == "preserve"


def test_interruption_does_not_lose_handoff_or_reset_again(tmp_path):
    project = ready_implementation(tmp_path)
    key, _ = worker(project)
    configure_policy(project, "implementation-handoff", reason="pilot")
    with (
        patch("driver_port_factory.codex.cli.CodexExecGateway.run", side_effect=KeyboardInterrupt),
        pytest.raises(KeyboardInterrupt),
    ):
        run_codex_stage(project, S.DRIVER_IMPLEMENTATION, context={},
                        backend=CodexBackend.EXEC, codex_bin="codex", model=None)
    session = read_session(project, key)
    result, decision = prepare_context(project, S.DRIVER_IMPLEMENTATION, key, session,
                                       continuing=False)
    assert result["handoff"] == session["handoff"]
    assert decision["decision"] == "preserve"
    assert not result.get("thread_id")


def test_analysis_boundary_handoff_survives_restart_and_preserves_execution(tmp_path):
    project = ready_implementation(tmp_path)
    key, session = worker(project)
    fresh, decision = prepare_context(
        project, S.TARGET_FRAMEWORK_ENABLEMENT, key, session, continuing=False)
    assert decision["decision"] == "rotate"
    assert fresh["automatic_boundary"] == "analysis_to_execution"
    packet = json.loads(project.artifacts.path_for_digest(fresh["handoff"]["digest"]).read_text())
    assert {"migration_contracts", "test_port_matrix"} <= {
        ref["kind"] for ref in packet["current_evidence"]}
    assert packet["history_lookup"]["previous_thread"] == "existing-worker"
    project = open_project(project.root)
    pending, decision = prepare_context(
        project, S.TARGET_FRAMEWORK_ENABLEMENT, key, read_session(project, key), continuing=False)
    assert pending == fresh and decision["decision"] == "preserve"
    save_session(project, key, "execution-worker", {})
    for stage in (S.TARGET_FRAMEWORK_ENABLEMENT, S.DRIVER_IMPLEMENTATION,
                  S.ARTIFACT_PREPARATION, S.PUBLIC_QEMU_VALIDATION):
        kept, decision = prepare_context(
            project, stage, key, read_session(project, key), continuing=False)
        assert kept["thread_id"] == "execution-worker"
        assert decision["decision"] == "preserve"


@pytest.mark.parametrize("case", ["repair", "started", "review_pending", "fresh"])
def test_analysis_boundary_skips_ineligible_calls_without_blocking(tmp_path, case):
    project = ready_implementation(tmp_path, reviewed=case != "review_pending")
    key, session = worker(project)
    if case == "started":
        (project.control / "codex/target_framework_enablement-old.metrics.json").write_text("{}")
    if case == "fresh":
        session = {}
    kept, decision = prepare_context(
        project, S.TARGET_FRAMEWORK_ENABLEMENT, key, session, continuing=case == "repair")
    assert kept == session and decision["decision"] == "preserve"


def test_legacy_project_without_policy_keeps_persistent_default(tmp_path):
    project = ready_implementation(tmp_path)
    (project.control / "codex/context-policy.json").unlink()
    assert read_policy(open_project(project.root))["name"] == "persistent"


def test_analysis_handoff_does_not_require_disabled_review(tmp_path):
    project = ready_implementation(tmp_path, reviewed=False)
    key, session = worker(project)
    with patch.object(type(project), "config", new_callable=PropertyMock,
                      return_value=replace(project.config, enable_analysis_review=False)):
        fresh, decision = prepare_context(
            project, S.TARGET_FRAMEWORK_ENABLEMENT, key, session, continuing=False)
    assert decision["decision"] == "rotate"
    assert not fresh.get("thread_id")


def test_reports_preserve_unknown_costs_and_do_not_certify_comparison(tmp_path, capsys):
    project = ready_implementation(tmp_path)
    directory = project.control / "codex"
    directory.mkdir(exist_ok=True)
    common = {"stage": "driver_implementation", "thread_id": "one", "model": "gpt-6-astra",
              "elapsed_seconds": 3, "started_at": "2026-09-26T00:00:00+00:00"}
    for i, extra in enumerate([
        {"reported_usage": [{"input_tokens": 100, "cached_input_tokens": 20,
                             "output_tokens": 10}], "resumed": False},
        {"reported_usage": [], "resumed": True},
    ]):
        (directory / f"driver_implementation-{i}.metrics.json").write_text(
            json.dumps({**common, **extra, "job_id": str(i)}))
    first = context_report(project)
    assert first["totals"]["known_usage_cache_hit_fraction"] == 0.2
    assert first["totals"]["unknown_usage_calls"] == 1
    assert first["totals"]["known_estimated_usd"] > 0
    assert first["quality"]["required_obligations_omitted"] is None
    assert "unrecorded" in first["by_policy"]
    compared = compare_reports(first, first)
    assert compared["both_costs_complete"] is False
    assert compared["savings_claim_supported"] is False
    assert main(["codex", "context-report", str(project.root), "--compare", str(project.root),
                 "--stage", "driver_implementation"]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["comparison_kind"] == "descriptive_only"
    assert main(["codex", "context-policy", str(project.root), "--policy",
                 "implementation-handoff"]) == 0
    assert json.loads(capsys.readouterr().out)["name"] == "implementation-handoff"
