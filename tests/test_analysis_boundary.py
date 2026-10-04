"""Offline phase handoff and frozen scope regressions; no paid model or device claims."""

import json
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import pytest

from driver_port_factory.acquisition.contracts import AcquisitionStage
from driver_port_factory.codex.cli import run_codex_stage
from driver_port_factory.codex.contracts import CodexBackend
from driver_port_factory.codex.gateway import CodexResult
from driver_port_factory.codex.policy import CodexExecutionPolicy
from driver_port_factory.codex.sessions import save_session, stage_session
from driver_port_factory.composition import open_project
from driver_port_factory.core.models import ProjectConfig, WorkflowError
from driver_port_factory.environment.contracts import EnvironmentStage
from driver_port_factory.intake.behavior_scope import effective, validate
from driver_port_factory.knowledge.bootstrap import KnowledgeBootstrapper
from driver_port_factory.migration.contracts import MigrationStage
from driver_port_factory.target_study.contracts import TargetStudyStage
from tests.knowledge_support import prepare_project
from tests.submission_support import submit
from tests.workflow_support import runner


def test_fresh_analysis_receives_frozen_inputs_and_resumes_its_own_conversation(tmp_path):
    project, _ = prepare_project(tmp_path)
    KnowledgeBootstrapper().build_infrastructure(project)
    study = TargetStudyStage.STUDY
    grant = CodexExecutionPolicy().grant(project, study)
    old_key, _ = stage_session(project, AcquisitionStage.EVIDENCE_CLOSURE, grant, None, "exec")
    save_session(project, old_key, "acquisition-with-large-history", {})
    expected = [None, "analysis-only"]

    def gateway(job):
        assert job.thread_id == expected.pop(0)
        payload = json.loads(job.prompt.split("<job>")[1].split("</job>")[0])
        refs = payload["reference_material"]
        packet = refs["analysis_entry"]
        assert packet["request"] and packet["device_identity"]["source_paths"]
        assert {r["role"] for r in packet["repositories"]} == {"source", "target", "qemu"}
        from driver_port_factory.short_refs import References

        references = References(project.root)
        assert all(
            references.get(r["evidence_ref"])["revision"] and r["path"]
            for r in packet["repositories"]
        )
        assert refs["functional_scope"]["boundary"]["mode"] == "source-driver"
        assert refs["functional_scope"]["boundary"]["integration"] == "target-kernel"
        assert "environment_evidence" in refs
        report = job.execution_root / "report.md"
        report.write_text("Synthetic blocked analysis; no semantic verdict.")
        submit(project, job, report, kind="report", decision="blocked")
        return CodexResult(job.job_id, "", "analysis-only")

    with patch("driver_port_factory.codex.cli.CodexExecGateway.run", side_effect=gateway):
        for _ in range(2):
            project = open_project(project.root)
            run_codex_stage(
                project,
                study,
                context={},
                backend=CodexBackend.EXEC,
                codex_bin="codex",
                model=None,
                skill_root=Path(__file__).resolve().parents[1] / "skill",
            )
    assert expected == []
    keys = {
        stage.value: stage_session(project, stage, grant, None, "exec")[0]
        for stage in (
            study,
            MigrationStage.CONTRACTS,
            EnvironmentStage.RECOVERY,
            MigrationStage.DRIVER_IMPLEMENTATION,
            MigrationStage.ARTIFACT_PREPARATION,
        )
    }
    assert keys[study.value] == keys[MigrationStage.CONTRACTS.value]
    assert keys[MigrationStage.DRIVER_IMPLEMENTATION.value] == old_key
    assert keys[MigrationStage.ARTIFACT_PREPARATION.value] == old_key
    assert len(set(keys.values())) == 3
    project.verify_integrity()


def test_scope_is_frozen_at_creation_and_resume_rejects_changed_operator_file(tmp_path):
    project, _ = prepare_project(tmp_path)
    options = runner(project).options
    destination = tmp_path / "new-run"
    scope = {
        "mode": "explicit-subset",
        "integration": "callback-harness",
        "required": ["notify according to device capabilities"],
        "excluded": ["real kernel panic hook installation"],
    }
    path = tmp_path / "scope.json"
    path.write_text(json.dumps(scope))
    from driver_port_factory.port import PortRunner

    service = PortRunner(replace(options, workspace=destination, behavior_scope_file=path))
    created = service._project()
    assert effective(created.config) == scope
    assert service._project().config.behavior_scope == scope
    path.write_text(json.dumps({**scope, "integration": "target-kernel"}))
    with pytest.raises(WorkflowError, match="behavior scope differs"):
        service._project()
    # Omitting a file on resume cannot erase or default the frozen subset.
    resumed = PortRunner(replace(service.options, behavior_scope_file=None))._project()
    assert effective(resumed.config) == scope


def test_legacy_session_policy_and_scope_validation(tmp_path):
    project, _ = prepare_project(tmp_path)
    raw = project.config.to_dict()
    raw.pop("scoped_worker_sessions")
    assert not ProjectConfig.from_dict(raw).scoped_worker_sessions
    scope = {
        "mode": "explicit-subset",
        "integration": "callback-harness",
        "required": ["operation"],
        "excluded": [],
    }
    for bad in (
        {**scope, "required": []},
        {**scope, "excluded": ["operation"]},
        {**scope, "integration": "anything"},
        {**scope, "required": [42]},
    ):
        with pytest.raises(WorkflowError):
            validate(bad)
