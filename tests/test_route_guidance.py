"""Offline controller fixtures: provenance, narrow invalidation and real scheduling boundaries."""

import json
from copy import deepcopy
from dataclasses import replace
from uuid import uuid4

import pytest

from driver_port_factory.core.models import WorkflowError
from driver_port_factory.migration import behavior, route, route_model
from driver_port_factory.migration.contracts import MigrationStage as S
from tests.target_support import write_route_fixture
from tests.workflow_support import ready_implementation


def test_source_only_route_needs_no_probe_and_evidence_addition_keeps_progress(tmp_path):
    report = tmp_path / "analysis.md"
    report.write_text("# Analysis\nSynthetic.\n")
    index = write_route_fixture(report)
    text = report.read_text()
    route_model.validate(index, text, ready=True)
    old = behavior.compile_plan(route_model.rows(index, text), route_model.bases(index, text))
    revised = deepcopy(index)
    revised["contracts"][0]["sources"].append(
        {"repository": "source", "path": "other.c", "line_start": 1, "line_end": 2}
    )
    new = behavior.compile_plan(route_model.rows(revised, text), route_model.bases(revised, text))
    assert [u["key"] for u in old] == [u["key"] for u in new]
    assert behavior.select(new, [u["key"] for u in old]) is None


def test_route_change_invalidates_only_referencing_behavior_and_dependents(tmp_path):
    report = tmp_path / "analysis.md"
    report.write_text("# Analysis\nSynthetic.\n")
    index = write_route_fixture(report)
    index["main_route"].append({"id": "R2", "section": "Other route"})
    index["behaviors"].append(
        {
            "id": "independent",
            "section": "Other behavior",
            "route": ["R2"],
            "contracts": ["C1"],
            "depends_on": [],
        }
    )
    text = (
        report.read_text()
        + "\n## Other route\nUnrelated owner.\n## Other behavior\nObserve other.\n"
    )
    original = behavior.compile_plan(route_model.rows(index, text), route_model.bases(index, text))
    changed = text.replace("fixture registration path", "changed registration path")
    new = behavior.compile_plan(route_model.rows(index, changed), route_model.bases(index, changed))
    assert [a["key"] == b["key"] for a, b in zip(original, new, strict=True)] == [
        False,
        False,
        True,
    ]


def running_project(tmp_path, monkeypatch):
    from driver_port_factory.composition import initialize_project

    monkeypatch.setattr(
        "tests.knowledge_support.initialize_project",
        lambda root, config: initialize_project(root, replace(config, behavior_scheduling=True)),
    )
    project = ready_implementation(tmp_path)
    project.start(S.DRIVER_IMPLEMENTATION)
    job = str(uuid4())
    folder = project.control / "codex"
    folder.mkdir(exist_ok=True)
    (folder / f"worker-{job}.metrics.json").write_text(
        json.dumps({"stage": S.DRIVER_IMPLEMENTATION.value, "invocation_state": "RUNNING"})
    )
    behavior.begin(project, S.DRIVER_IMPLEMENTATION, job)
    return project, job


def test_revision_cannot_weaken_contract_or_complete_a_replaced_objective(tmp_path, monkeypatch):
    project, job = running_project(tmp_path, monkeypatch)
    original, text, _ = route.current(project)
    report = project.root / "revised.md"
    report.with_suffix(".route.json").write_text(json.dumps(original["index"]))
    report.write_text(text.replace("compare the returned value", "always accept"))
    with pytest.raises(WorkflowError, match="frozen source obligations"):
        route.revise(project, job, report)
    report.write_text(
        text.replace("Initialize the synthetic device", "Initialize a changed synthetic objective")
    )
    result = route.revise(project, job, report)
    assert result["status"] == "REVISED_NOT_ACCEPTED"
    monkeypatch.setattr(behavior, "checkpoint", lambda p: None)
    behavior.finish(
        project,
        S.DRIVER_IMPLEMENTATION,
        {"job_id": job, "decision": "operation", "operation": "behavior_done"},
    )
    assert behavior._load(project)["completed"] == []
    assert behavior.packet(project, S.DRIVER_IMPLEMENTATION)["current"]["id"] == "init"
    assert project.stage(S.DRIVER_IMPLEMENTATION).status.value == "RUNNING"


def test_invalid_source_range_and_open_premise_are_not_accepted(tmp_path):
    project = ready_implementation(tmp_path)
    original, text, _ = route.current(project)
    bad = deepcopy(original["index"])
    bad["contracts"][0]["sources"][0]["line_end"] = 10000
    with pytest.raises(WorkflowError, match="beyond"):
        route.freeze(project, bad, text)
    bad = deepcopy(original["index"])
    bad["premises"] = [
        {
            "id": "P1",
            "section": "Fixture route",
            "question_section": "Fixture route",
            "route": ["R1"],
            "status": "open",
            "sources": [],
            "probe_receipts": [],
        }
    ]
    with pytest.raises(WorkflowError, match="design-changing"):
        route.freeze(project, bad, text)


def test_probe_failure_is_archived_success_deduplicates_and_stale_input_is_rejected(
    tmp_path, monkeypatch
):
    from driver_port_factory.core.execution import CommandRunner
    from driver_port_factory.migration import route_probe
    from driver_port_factory.platform import service

    project = ready_implementation(tmp_path)
    original, text, _ = route.current(project)
    index = deepcopy(original["index"])
    index["premises"] = [
        {
            "id": "premise",
            "section": "Probe question",
            "question_section": "Probe question",
            "route": ["R1"],
            "status": "open",
            "sources": [],
            "probe_receipts": [],
        }
    ]
    text += (
        "\n## Probe question\nDetermine fixture exit condition; "
        "false prevents this fixture route.\n"
    )
    report = project.root / "draft.md"
    report.write_text(text)
    report.with_suffix(".route.json").write_text(json.dumps(index))
    worktree, revision = service.location(project)
    profile = {"target_revision": revision, "cache_key": "fixture"}
    monkeypatch.setattr(service, "verified", lambda p: {})
    monkeypatch.setattr(service, "load", lambda p: (profile, worktree))
    monkeypatch.setattr(service, "check_image", lambda *args: None)
    calls = []

    def execute(profile, root, directory, command, **kwargs):
        calls.append(command)
        # Real local subprocess only, synthetic probe; no Docker or model invocation.
        return CommandRunner(directory).run(command, cwd=root, timeout_seconds=kwargs["timeout"])

    monkeypatch.setattr(route_probe.executor, "container", execute)
    script = worktree / ".dpf-output/probe.sh"
    script.parent.mkdir(exist_ok=True)
    script.write_text("exit 1\n")
    failed = route_probe.run(project, report, "premise", script)
    assert failed["status"] == "FAILED"
    assert route_probe.receipt(project, failed["id"])["result"]["exit_code"] == 1
    script.write_text("printf 'fixture observation\\n'\nexit 0\n")
    observed = route_probe.run(project, report, "premise", script)
    assert observed["status"] == "OBSERVED"
    assert route_probe.run(project, report, "premise", script)["reused"]
    assert len(calls) == 2
    index["premises"][0].update(status="supported", probe_receipts=[failed["id"]])
    with pytest.raises(WorkflowError, match="Failed or stale"):
        route.freeze(project, index, text)
    index["premises"][0]["probe_receipts"] = [observed["id"]]
    route.freeze(project, index, text)
    (worktree / "src/driver-api.rs").write_text("changed prerequisite\n")
    with pytest.raises(WorkflowError, match="Failed or stale"):
        route.freeze(project, index, text)


def test_selected_learning_is_retrievable_but_does_not_change_bound_snapshot(tmp_path, monkeypatch):
    from driver_port_factory.knowledge import route_learning, shared
    from driver_port_factory.knowledge.shared_store import Library

    project = ready_implementation(tmp_path)
    library = Library(tmp_path / "shared")
    with library.writing():
        old = library.commit()["snapshot"]
    monkeypatch.setattr(
        route_learning, "project_binding", lambda p: {"root": str(library.root), "snapshot": old}
    )
    original, text, _ = route.current(project)
    index = deepcopy(original["index"])
    index["premises"] = [
        {
            "id": "api",
            "section": "Lifetime finding",
            "question_section": "Lifetime finding",
            "route": ["R1"],
            "status": "supported",
            "probe_receipts": [],
            "sources": [
                {
                    "repository": "target",
                    "path": "src/driver-api.rs",
                    "line_start": 1,
                    "line_end": 2,
                }
            ],
        }
    ]
    index["learn"] = ["api"]
    text += "\n## Lifetime finding\nFixture callback registration; synthetic evidence only.\n"
    binding = route.freeze(project, index, text)
    result = route_learning.publish(project, binding, text)
    assert result["status"] == "PUBLISHED_INTERPRETATIONS"
    assert library.snapshot(old)[1]["entries"] == {}
    found = shared.search(library, "Fixture callback registration")
    assert any(r["kind"] == "EXPERIENCE" for r in found["results"])
    head = library.head()
    route_learning.publish(project, binding, text)
    assert library.head() == head


def test_normal_source_edits_do_not_reset_behavior_progress(tmp_path, monkeypatch):
    from driver_port_factory.platform.service import location

    project, job = running_project(tmp_path, monkeypatch)
    original, text, _ = route.current(project)
    index = deepcopy(original["index"])
    index["contracts"][0]["sources"].append(
        {"repository": "target", "path": "src/driver-api.rs", "line_start": 1, "line_end": 2}
    )
    report = project.root / "evidence-update.md"
    report.write_text(text)
    report.with_suffix(".route.json").write_text(json.dumps(index))
    route.revise(project, job, report)
    monkeypatch.setattr(behavior, "checkpoint", lambda p: None)
    behavior.finish(
        project,
        S.DRIVER_IMPLEMENTATION,
        {"job_id": job, "decision": "operation", "operation": "behavior_done"},
    )
    before = behavior.packet(project, S.DRIVER_IMPLEMENTATION)
    worktree, _ = location(project)
    api = worktree / "src/driver-api.rs"
    api.write_text(api.read_text() + "// Normal implementation edit.\n")
    next_job = str(uuid4())
    (project.control / "codex" / f"worker-{next_job}.metrics.json").write_text(
        json.dumps({"stage": S.DRIVER_IMPLEMENTATION.value, "invocation_state": "RUNNING"})
    )
    behavior.begin(project, S.DRIVER_IMPLEMENTATION, next_job)
    route.revise(project, next_job, report)
    after = behavior.packet(project, S.DRIVER_IMPLEMENTATION)
    assert before["current"]["id"] == after["current"]["id"] == "operation"
    assert before["current"]["key"] == after["current"]["key"]
    assert len(behavior._load(project)["completed"]) == 1


def test_route_correction_inside_same_behavior_does_not_require_an_extra_round(
    tmp_path, monkeypatch
):
    project, job = running_project(tmp_path, monkeypatch)
    original, text, _ = route.current(project)
    report = project.root / "corrected.md"
    report.with_suffix(".route.json").write_text(json.dumps(original["index"]))
    report.write_text(text.replace("fixture registration path", "corrected registration path"))
    route.revise(project, job, report)
    monkeypatch.setattr(behavior, "checkpoint", lambda p: None)
    behavior.finish(
        project,
        S.DRIVER_IMPLEMENTATION,
        {"job_id": job, "decision": "operation", "operation": "behavior_done"},
    )
    assert len(behavior._load(project)["completed"]) == 1
    assert behavior.packet(project, S.DRIVER_IMPLEMENTATION)["current"]["id"] == "operation"
