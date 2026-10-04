"""Offline protocol regressions; no model, Docker or real-driver validation."""

import json
from copy import deepcopy
from uuid import uuid4

import pytest

from driver_port_factory.codex.policy import CodexExecutionPolicy
from driver_port_factory.codex.submission import receipt_path, write_submission
from driver_port_factory.core.models import WorkflowError
from driver_port_factory.migration import route, route_model
from driver_port_factory.target_study.contracts import TargetStudyStage as S
from driver_port_factory.target_study.service import TargetStudyService
from tests.test_target_knowledge_quality import ready


def compact_report(project, original):
    folder = CodexExecutionPolicy().grant(project, S.STUDY).execution_root
    report = folder / "report.md"
    index = json.loads(original.with_suffix(".route.json").read_text())
    text = original.read_text()
    for group in ("main_route", "behaviors", "contracts"):
        for row in index[group]:
            text = text.replace("## " + row.pop("section") + "\n", "## " + row["id"] + "\n")
            if row.get("depends_on") == []:
                del row["depends_on"]
    del index["premises"], index["learn"]
    report.write_text(text)
    report.with_suffix(".route.json").write_text(json.dumps(index))
    return report, index


def test_batch_feedback_and_same_job_resubmit_without_new_worker_or_empty_sidecar(tmp_path):
    project, original = ready(tmp_path)
    report, index = compact_report(project, original)
    index["premises"] = [
        {
            "id": "P1",
            "route": ["R1"],
            "status": "supported",
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
    good = deepcopy(index)
    text = report.read_text() + "\n## P1\nSynthetic source-supported route premise.\n"
    report.write_text(text)
    index["premises"][0].update(
        question_section="Absent question heading", probe_receipts=["Source prose is not a receipt"]
    )
    index["contracts"][0]["sources"][0]["line_end"] = 999999
    report.with_suffix(".route.json").write_text(json.dumps(index))
    job = str(uuid4())
    kwargs = {"job_id": job, "file_path": str(report), "kind": "report", "decision": "pass"}
    with pytest.raises(WorkflowError) as caught:
        write_submission(project, S.STUDY, **kwargs)
    error = str(caught.value)
    assert "Absent question heading" in error
    assert "probe_receipts" in error
    assert "beyond its source" in error
    assert "same turn" in error
    assert not receipt_path(project, job).exists()
    assert project.stage(S.STUDY).status.value == "RUNNING"

    # Same job, only index fixes; neither a restart nor a probes.json is needed.
    report.with_suffix(".route.json").write_text(json.dumps(good))
    saved = write_submission(project, S.STUDY, **kwargs)
    frozen = json.loads(saved.read_text())
    assert frozen["knowledge_probe_specification"] == {"mode": "focused", "probes": []}
    assert frozen["route_index"]["premises"][0]["probe_receipts"] == []
    assert frozen["route_index"]["behaviors"][0]["depends_on"] == []
    assert project.stage(S.STUDY).status.value == "RUNNING"  # Submission cannot self-approve.
    TargetStudyService().accept(
        project,
        report,
        specification=frozen["knowledge_probe_specification"],
        route_index=frozen["route_index"],
    )
    assert project.stage(S.STUDY).status.value == "PASS"
    project.verify_integrity()


def test_submit_catches_forged_receipt_and_optional_malformed_sidecar_together(tmp_path):
    project, original = ready(tmp_path)
    report, index = compact_report(project, original)
    report.write_text(report.read_text() + "\n## P1\nUnobserved fixture claim.\n")
    index["premises"] = [
        {"id": "P1", "route": ["R1"], "status": "supported", "probe_receipts": ["P99"]}
    ]
    report.with_suffix(".route.json").write_text(json.dumps(index))
    report.with_suffix(".probes.json").write_text("{broken")
    job = str(uuid4())
    with pytest.raises(WorkflowError) as caught:
        write_submission(
            project, S.STUDY, job_id=job, file_path=str(report), kind="report", decision="pass"
        )
    assert "Unknown controller probe receipt: P99" in str(caught.value)
    assert ".probes.json" in str(caught.value)
    assert not receipt_path(project, job).exists()


def test_independent_structural_errors_are_reported_together_without_guessing(tmp_path):
    project, original = ready(tmp_path)
    report, index = compact_report(project, original)
    index["behaviors"][0]["route"] = ["unavailable"]
    index["behaviors"][1]["contracts"] = ["missing"]
    index["contracts"][0]["sources"][0]["repository"] = []
    index["premises"] = [{"id": "P1", "status": "supported", "route": ["R1"]}]
    with pytest.raises(WorkflowError) as caught:
        route.freeze(project, index, report.read_text())
    error = str(caught.value)
    for expected in (
        "Unknown route",
        "Unknown contracts",
        "Invalid source citation",
        "P1.section",
        "requires source or observed evidence",
    ):
        assert expected in error
    # Validation cannot invent sources, match similar headings or rewrite user data.
    assert index["premises"][0] == {"id": "P1", "status": "supported", "route": ["R1"]}


def test_compact_defaults_preserve_behavior_plan_and_do_not_mutate_input(tmp_path):
    project, original = ready(tmp_path)
    report, compact = compact_report(project, original)
    snapshot = deepcopy(compact)
    expanded = route_model.validate(compact, report.read_text(), ready=True)
    from driver_port_factory.migration.behavior import compile_plan

    plan = compile_plan(
        route_model.rows(expanded, report.read_text()),
        route_model.bases(expanded, report.read_text()),
    )
    assert [u["id"] for u in plan] == ["init", "operation"]
    assert plan[1]["depends_on"] == ["init"]
    assert compact == snapshot
