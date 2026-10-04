"""Offline real-controller boundaries; synthetic driver and model, no paid calls."""

import json
import sqlite3
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import pytest

from driver_port_factory.codex.check_mcp import dispatch
from driver_port_factory.codex.gateway import CodexResult
from driver_port_factory.composition import initialize_project, open_project
from driver_port_factory.core.models import EvaluationMode, StageStatus, WorkflowError
from driver_port_factory.migration import behavior
from driver_port_factory.migration.check_tools import check
from driver_port_factory.migration.contracts import MigrationArtifact as A
from driver_port_factory.migration.contracts import MigrationStage as S
from driver_port_factory.migration.local_adaptation import eligible
from driver_port_factory.target_study.contracts import TargetStudyStage as TS
from tests.migration_support import delivery_fixture
from tests.submission_support import submit
from tests.workflow_support import ready_implementation, runner


def ready(tmp_path):
    def initialize(root, config):
        return initialize_project(
            root, replace(config, behavior_scheduling=True, unified_implementation=True)
        )

    with patch("tests.knowledge_support.initialize_project", side_effect=initialize):
        return ready_implementation(tmp_path)


def retried(project):
    with sqlite3.connect(project.database_path) as db:
        return db.execute(
            "SELECT count(*) FROM events WHERE event_type='stage.retried'"
        ).fetchone()[0]


def test_platform_correction_retains_completed_work_session_and_frozen_obligations(tmp_path):
    project = ready(tmp_path)
    contract = project.artifact(S.CONTRACTS, A.CONTRACTS)
    calls = []
    retained = []

    def work(job):
        packet = json.loads(job.prompt.split("<job>")[1].split("</job>")[0])["reference_material"]
        current = packet["behavior_progress"]["current"]
        calls.append(current["id"] if current else None)
        if len(calls) > 1:
            assert job.thread_id == "continuous-worker"
        if len(calls) <= 3:
            assert "# Checks inside one behavior" in job.prompt
            assert "# Execution interface" not in job.prompt
        else:
            assert "# Execution interface" in job.prompt
        report = job.execution_root / ".dpf-output/report.md"
        report.parent.mkdir(exist_ok=True)
        if len(calls) == 1:
            (job.execution_root / "driver.rs").write_text("pub fn driver() {}\n")
            dispatch(project, job.job_id, "progress", {"status": "done"})
            with pytest.raises(WorkflowError, match="already submitted"):
                dispatch(project, job.job_id, "progress", {"status": "done"})
        elif len(calls) == 2:
            retained.extend(behavior._load(project)["completed"])
            report.write_text(
                "Synthetic runtime counterexample: target mapping requires assignment.\n"
            )
            submit(
                project,
                job,
                report,
                kind="report",
                decision="rework",
                repair_stage="target_platform_study",
            )
        elif len(calls) == 3:
            # Reopen through the actual validators while the corrected work is unfinished.
            reopened = open_project(project.root)
            assert reopened.stage(TS.STUDY).status is StageStatus.PASS
            assert reopened.stage(S.CONTRACTS).status is StageStatus.PASS
            assert reopened.stage(S.DRIVER_IMPLEMENTATION).status is StageStatus.RUNNING
            assert reopened.artifact(S.CONTRACTS, A.CONTRACTS).digest == contract.digest
            assert behavior._load(reopened)["completed"] == retained
            assert retried(reopened) == 0
            finding = packet["behavior_progress"]["local_adaptation"][0]["report"]
            assert "requires assignment" in Path(finding["path"]).read_text()
            assert (job.execution_root / "driver.rs").exists()
            (job.execution_root / "driver.rs").write_text("pub fn driver() { /* adapted */ }\n")
            delivery_fixture(job.execution_root)
            assert "PASS" in check(project, job.job_id, {})
            dispatch(
                project, job.job_id, "progress", {"status": "done", "note": "Resolved premise."}
            )
        else:
            pytest.fail("No separate delivery model turn")
        return CodexResult(job.job_id, "", "continuous-worker")

    with patch("driver_port_factory.codex.cli.CodexExecGateway.run", side_effect=work):
        runner(project)._implementation(project)
    assert calls == ["init", "operation", "operation"]
    assert project.stage(S.DRIVER_IMPLEMENTATION).status is StageStatus.PASS
    assert retried(project) == 0
    state = behavior._load(project)
    assert len(state["completed"]) == 2 and len(state["local_adaptations"]) == 1
    open_project(project.root).verify_integrity()


def test_repeated_local_rework_without_source_changes_blocks_without_reset(tmp_path):
    project = ready(tmp_path)
    calls = []

    def work(job):
        calls.append(job.job_id)
        if len(calls) == 1:
            (job.execution_root / "driver.rs").write_text("pub fn driver() {}\n")
        report = job.execution_root / ".dpf-output/report.md"
        report.parent.mkdir(exist_ok=True)
        report.write_text(f"Same unresolved target premise, wording {len(calls)}.\n")
        submit(
            project,
            job,
            report,
            kind="report",
            decision="rework",
            repair_stage="target_platform_study",
        )
        return CodexResult(job.job_id, "", "same-worker")

    finish = behavior.finish
    crashed = False

    def interrupted(*args, **kwargs):
        nonlocal crashed
        if not crashed:
            crashed = True
            raise OSError("synthetic crash after recording local correction")
        return finish(*args, **kwargs)

    with (
        patch("driver_port_factory.codex.cli.CodexExecGateway.run", side_effect=work),
        patch.object(behavior, "finish", side_effect=interrupted),
    ):
        with pytest.raises(OSError, match="synthetic crash"):
            runner(project)._implementation(project)
        project = open_project(project.root)
        runner(project)._implementation(project)
    assert len(calls) == 3
    assert len(behavior._load(project)["local_adaptations"]) == 2
    assert project.stage(S.DRIVER_IMPLEMENTATION).status is StageStatus.BLOCKED
    assert project.stage(TS.STUDY).status is StageStatus.PASS
    assert retried(project) == 0
    assert behavior._load(open_project(project.root))["completed"] == []


def test_local_route_never_replaces_contract_or_environment_revision(tmp_path):
    project = ready(tmp_path)
    assert eligible(project.config, S.DRIVER_IMPLEMENTATION, TS.STUDY)
    for target in (S.CONTRACTS, S.ARTIFACT_PREPARATION):
        assert not eligible(project.config, S.DRIVER_IMPLEMENTATION, target)
    assert not eligible(
        replace(project.config, behavior_scheduling=False), S.DRIVER_IMPLEMENTATION, TS.STUDY
    )
    assert not eligible(
        replace(project.config, evaluation_mode=EvaluationMode.POST_HOC_SEALED_BLIND),
        S.DRIVER_IMPLEMENTATION,
        TS.STUDY,
    )
