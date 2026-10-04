"""Behavior progress is bounded, dependency-aware and separate from acceptance."""

import json
from dataclasses import replace
from unittest.mock import patch

import pytest

from driver_port_factory.codex.gateway import CodexResult
from driver_port_factory.core.models import StageStatus, WorkflowError
from driver_port_factory.migration import behavior
from driver_port_factory.migration.contracts import MigrationStage as S
from tests.submission_support import submit
from tests.workflow_support import ready_implementation, runner


def row(name, deps=()):
    return {
        "id": name,
        "outcome": "observable " + name,
        "contracts": ["C1"],
        "depends_on": list(deps),
        "constraints": ["preserve cleanup"],
    }


def test_cycles_merge_and_edited_prerequisite_invalidates_dependent_progress():
    plan = behavior.compile_plan(
        [row("a", ["b"]), row("b", ["a"]), row("c", ["b"])], {n: "basis" for n in ("a", "b", "c")}
    )
    assert len(plan) == 2
    assert [b["id"] for b in plan[0]["behaviors"]] == ["a", "b"]
    completed = [p["key"] for p in plan]
    assert behavior.select(plan, completed) is None
    edited = row("a", ["b"])
    edited["outcome"] = "changed behavior"
    updated = behavior.compile_plan(
        [edited, row("b", ["a"]), row("c", ["b"])], {n: "basis" for n in ("a", "b", "c")}
    )
    assert all(old["key"] != new["key"] for old, new in zip(plan, updated, strict=True))
    assert behavior.select(updated, completed)["id"] == "a"
    with pytest.raises(WorkflowError, match="Unknown"):
        behavior.compile_plan([row("a", ["missing"])], {n: "basis" for n in ("a", "b", "c")})


def test_active_item_continues_and_done_cannot_skip_its_prerequisite():
    plan = behavior.compile_plan(
        [row("a"), row("b", ["a"]), row("c")], {n: "basis" for n in ("a", "b", "c")}
    )
    assert behavior.select(plan, [plan[1]["key"]])["id"] == "a"
    assert behavior.select(plan, [], plan[2]["key"])["id"] == "c"


@pytest.mark.parametrize("unified", [False, True])
def test_real_runner_keeps_one_behavior_per_round_then_runs_normal_implementation_gate(
    tmp_path, unified
):
    from driver_port_factory.composition import initialize_project

    def initialize(root, config):
        return initialize_project(
            root, replace(config, behavior_scheduling=True, unified_implementation=unified)
        )

    with patch("tests.knowledge_support.initialize_project", side_effect=initialize):
        project = ready_implementation(tmp_path)
    calls = []

    def work(job):
        packet = json.loads(job.prompt.split("<job>")[1].split("</job>")[0])["reference_material"]
        assert {
            "migration_contracts",
            "test_port_matrix",
            "migration_handoff",
            "target_study_report",
        } <= packet["frozen_inputs"].keys()
        assert packet["workspace_paths"]["target_worktree"] == str(job.execution_root)
        progress = packet["behavior_progress"]
        instructions = json.loads(job.prompt.split("<job>")[1].split("</job>")[0])["instructions"]
        assert instructions["objective"] == progress["objective"]
        current = progress["current"]
        if calls:
            assert job.thread_id == "same-worker"
        calls.append(current["id"] if current else None)
        output = job.execution_root / ".dpf-output"
        output.mkdir(exist_ok=True)
        report = output / "report.md"
        report.write_text("Synthetic progress; not driver acceptance.\n")
        if len(calls) == 1:
            (job.execution_root / "driver.rs").write_text("pub fn init() {}\n")
            # Framework changes belong to the selected behavior, without another stage/job.
            api = job.execution_root / "src/driver-api.rs"
            api.write_text(api.read_text() + "pub fn needed_by_init() {}\n")
        if len(calls) > 1:
            assert "needed_by_init" in (job.execution_root / "src/driver-api.rs").read_text()
        if len(calls) <= 3:
            before = behavior._load(project)
            refused = behavior.finish(
                project, S.DRIVER_IMPLEMENTATION, {"job_id": job.job_id, "decision": "pass"}
            )
            assert "cannot skip" in refused
            assert behavior._load(project) == before
            assert project.stage(S.DRIVER_IMPLEMENTATION).status is StageStatus.RUNNING
            if len(calls) == 3:
                from driver_port_factory.migration.check_tools import check
                from tests.migration_support import delivery_fixture

                delivery_fixture(job.execution_root)
                assert "PASS" in check(project, job.job_id, {})
            submit(
                project,
                job,
                report,
                kind="report",
                decision="operation",
                operation="behavior_continue" if len(calls) == 1 else "behavior_done",
            )
        else:
            pytest.fail("Final package must not require a separate delivery model call")
        return CodexResult(job.job_id, "", "same-worker")

    with patch("driver_port_factory.codex.cli.CodexExecGateway.run", side_effect=work):
        runner(project)._implementation(project)
    assert calls == ["init", "init", "operation"]
    assert project.stage(S.DRIVER_IMPLEMENTATION).status is StageStatus.PASS
    state = behavior._load(project)
    assert state["checkpoint"] and len(state["completed"]) == 2
    assert project.stage(S.ARTIFACT_PREPARATION).status is StageStatus.READY
    # Editing the derived progress display cannot forge a completed unit.
    (project.control / "behavior-progress.json").write_text('{"completed": ["forged"]}')
    assert behavior._load(project) == state


@pytest.mark.parametrize("option", [None, False, True])
def test_new_developer_run_defaults_to_behaviors_but_resume_keeps_frozen_setting(tmp_path, option):
    from driver_port_factory.codex.contracts import CodexBackend
    from driver_port_factory.composition import open_project
    from driver_port_factory.port import PortOptions, PortRunner

    options = PortOptions(
        workspace=tmp_path / "run",
        source_platform="source",
        target_platform="target",
        driver_name="driver",
        skill_root=tmp_path,
        catalogs=(),
        backend=CodexBackend.EXEC,
        codex_bin="codex",
        model=None,
        behavior_scheduling=option,
    )
    project = PortRunner(options)._project()
    assert project.config.behavior_scheduling is (option is not False)
    assert S.TARGET_FRAMEWORK_ENABLEMENT.value not in project.workflow.stage_values
    resumed = PortRunner(replace(options, behavior_scheduling=None))._project()
    assert resumed.config.behavior_scheduling == project.config.behavior_scheduling
    open_project(project.root).verify_integrity()
    with pytest.raises(WorkflowError, match="persisted"):
        PortRunner(
            replace(options, behavior_scheduling=not project.config.behavior_scheduling)
        )._project()


def test_missing_legacy_flag_does_not_enable_scheduling():
    from driver_port_factory.core.models import ProjectConfig
    from tests.test_optional_reviews import config

    value = config().to_dict()
    value.pop("behavior_scheduling")
    assert ProjectConfig.from_dict(value).behavior_scheduling is False


@pytest.mark.parametrize(
    "mode,role,expected",
    [
        ("developer-evidence", "developer", True),
        ("post-hoc-sealed-blind", "migration_operator", False),
    ],
)
def test_init_chooses_role_appropriate_default(tmp_path, mode, role, expected):
    from driver_port_factory.cli import main
    from driver_port_factory.composition import open_project

    root = tmp_path / "run"
    assert (
        main(
            [
                "init",
                str(root),
                "--source",
                "source",
                "--target",
                "target",
                "--driver",
                "driver",
                "--mode",
                mode,
                "--role",
                role,
            ]
        )
        == 0
    )
    assert open_project(root).config.behavior_scheduling is expected
