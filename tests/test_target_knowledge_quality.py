"""Knowledge gates prove actual source retrieval, never semantic entailment."""

import json
from copy import deepcopy

import pytest

from driver_port_factory.core.models import FileArtifact, GeneratedArtifact, WorkflowError
from driver_port_factory.knowledge.bootstrap import KnowledgeBootstrapper
from driver_port_factory.knowledge.index import KnowledgeIndex
from driver_port_factory.knowledge.probes import TARGET_TOPICS, evaluate, for_report
from driver_port_factory.target_study.contracts import TargetStudyArtifact as A
from driver_port_factory.target_study.contracts import TargetStudyStage as S
from driver_port_factory.target_study.service import TargetStudyService
from tests.knowledge_support import prepare_project
from tests.target_support import write_probe_fixture


def ready(tmp_path):
    project, _ = prepare_project(tmp_path)
    KnowledgeBootstrapper().build_infrastructure(project)
    project.start(S.STUDY)
    report = project.root / "study.md"
    report.write_text("Synthetic target study; original relevance requires review.\n")
    write_probe_fixture(project, report)
    return project, report


def test_report_alone_and_missing_topics_cannot_pass(tmp_path):
    project, report = ready(tmp_path)
    with pytest.raises(WorkflowError):
        project.finalize_stage(S.STUDY, (FileArtifact(A.REPORT, report),))
    spec_path = report.with_suffix(".probes.json")
    spec = json.loads(spec_path.read_text())
    spec["probes"].pop()
    spec_path.write_text(json.dumps(spec))
    with pytest.raises(WorkflowError, match="cover each"):
        TargetStudyService().accept(project, report)
    assert project.stage(S.STUDY).status.value == "RUNNING"


def test_forged_query_result_or_out_of_range_original_is_rejected(tmp_path):
    project, report = ready(tmp_path)
    original = json.loads(report.with_suffix(".probes.json").read_text())
    for edit, match in (
        ({"query": "nonexistent_unique_symbol"}, "not retrieved"),
        ({"applicability": "UNKNOWN"}, "Unknown target requirement"),
    ):
        spec = deepcopy(original)
        spec["probes"][0].update(edit)
        with pytest.raises(WorkflowError, match=match):
            evaluate(KnowledgeIndex.for_project(project), spec, report.read_bytes())
    spec = deepcopy(original)
    spec["probes"][0]["evidence"][0]["line_end"] = 100000
    with pytest.raises(WorkflowError, match="outside"):
        evaluate(KnowledgeIndex.for_project(project), spec, report.read_bytes())


def test_receipt_cannot_be_reused_for_changed_report_or_bypass_bundle(tmp_path):
    project, report = ready(tmp_path)
    receipt = for_report(project, report)
    assert len(receipt["observations"]) == len(TARGET_TOPICS)
    assert receipt["semantic_status"] == "SEMANTICS_NOT_MECHANICALLY_VERIFIED"
    report.write_text(report.read_text() + "Changed API claim.\n")
    with pytest.raises(WorkflowError, match="stale"):
        project.finalize_stage(
            S.STUDY,
            (
                FileArtifact(A.REPORT, report),
                route_artifact(project, report),
                GeneratedArtifact(A.KNOWLEDGE_QUALITY, json.dumps(receipt).encode(), "test:stale"),
            ),
        )
    TargetStudyService().accept(project, report)
    assert project.stage(S.STUDY).status.value == "PASS"
    project.verify_integrity()


def test_frozen_submission_does_not_reread_mutated_probe_sidecar(tmp_path):
    from uuid import uuid4

    from driver_port_factory.codex.submission import write_submission

    project, report = ready(tmp_path)
    from driver_port_factory.codex.policy import CodexExecutionPolicy

    destination = CodexExecutionPolicy().grant(project, S.STUDY).execution_root
    destination.mkdir(parents=True, exist_ok=True)
    moved = destination / report.name
    moved.write_bytes(report.read_bytes())
    moved.with_suffix(".route.json").write_bytes(report.with_suffix(".route.json").read_bytes())
    moved.with_suffix(".probes.json").write_bytes(report.with_suffix(".probes.json").read_bytes())
    report = moved
    receipt = write_submission(
        project, S.STUDY, job_id=str(uuid4()), file_path=str(report), kind="report", decision="pass"
    )
    frozen = json.loads(receipt.read_text())
    report.with_suffix(".probes.json").write_text('{"probes": []}')
    report.with_suffix(".route.json").write_text('{"behaviors": []}')
    TargetStudyService().accept(
        project,
        report,
        specification=frozen["knowledge_probe_specification"],
        route_index=frozen["route_index"],
    )
    assert project.stage(S.STUDY).status.value == "PASS"


def test_malformed_probe_values_are_workflow_errors(tmp_path):
    project, report = ready(tmp_path)
    original = json.loads(report.with_suffix(".probes.json").read_text())
    for key, value in (
        ("applicability", []),
        ("evidence", [{"chunk_id": [], "line_start": 1, "line_end": 1}]),
    ):
        spec = deepcopy(original)
        spec["probes"][0][key] = value
        with pytest.raises(WorkflowError):
            evaluate(KnowledgeIndex.for_project(project), spec, report.read_bytes())


def test_focused_queries_keep_provenance_without_a_topic_survey(tmp_path):
    project, report = ready(tmp_path)
    index = KnowledgeIndex.for_project(project)
    original = json.loads(report.with_suffix(".probes.json").read_text())
    spec = {"mode": "focused", "probes": original["probes"][:1]}
    receipt = evaluate(index, spec, report.read_bytes())
    assert len(receipt["observations"]) == 1
    bad = deepcopy(spec)
    bad["probes"][0]["query"] = "nonexistent_unique_symbol"
    with pytest.raises(WorkflowError, match="not retrieved"):
        evaluate(index, bad, report.read_bytes())
    bad = deepcopy(spec)
    bad["probes"] *= 2
    with pytest.raises(WorkflowError, match="unique"):
        evaluate(index, bad, report.read_bytes())
    TargetStudyService().accept(project, report, specification=spec)
    project.verify_integrity()


def test_no_retrieval_is_explicit_and_cannot_claim_retrieval_or_semantics(tmp_path):
    project, report = ready(tmp_path)
    spec = {"mode": "focused", "probes": []}
    receipt = evaluate(KnowledgeIndex.for_project(project), spec, report.read_bytes())
    assert receipt["status"] == "NO_RETRIEVAL_REQUESTED"
    assert receipt["semantic_status"] == "SEMANTICS_NOT_MECHANICALLY_VERIFIED"
    forged = {**receipt, "status": "RETRIEVAL_VALIDATED"}
    with pytest.raises(WorkflowError, match="misstates"):
        project.finalize_stage(
            S.STUDY,
            (
                FileArtifact(A.REPORT, report),
                route_artifact(project, report),
                GeneratedArtifact(A.KNOWLEDGE_QUALITY, json.dumps(forged).encode(), "test:forged"),
            ),
        )
    TargetStudyService().accept(project, report, specification=spec)
    project.verify_integrity()


def route_artifact(project, report):
    from driver_port_factory.migration.route import encoded, freeze, read_index

    return GeneratedArtifact(
        A.ROUTE, encoded(freeze(project, read_index(report), report.read_text())), "test:route"
    )
