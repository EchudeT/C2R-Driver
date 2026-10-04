"""Offline regressions for pvpanic's evidence-gap and proposal/report retry loop."""

import json
from uuid import uuid4

import pytest

from driver_port_factory.acquisition.accounting import gap_reason
from driver_port_factory.acquisition.closure import EvidenceClosureFinalizer
from driver_port_factory.acquisition.facets import parse_facet
from driver_port_factory.codex.contracts import CodexArtifact
from driver_port_factory.codex.submission import write_submission
from driver_port_factory.composition import open_project
from driver_port_factory.core.checker_decision import (
    CheckerDecisionRequired,
    RecoveryPaused,
    clear_pending,
    pending_decision,
    request_recovery,
)
from driver_port_factory.core.models import GeneratedArtifact, WorkflowError
from tests.acquisition_support import import_evidence_proposal
from tests.test_evidence_reuse import A, S, local_proposal, ready


def proposal_with_available_tests(project):
    proposal = local_proposal(project)
    source = next(f for f in proposal["facets"] if f["lane"] == "source")
    test = next(f for f in proposal["facets"] if f["lane"] == "test")
    test["locators"] = source["locators"]
    test["gap"].pop("reason", None)
    test["gap"]["basis"] = [{"lane": "source", "facet": source["facet"]}]
    test["gap"]["impact"] = "Available source defines behavior; no dedicated test is established"
    return proposal


def test_available_material_and_bounded_limitation_survive_capture_and_reopen(tmp_path):
    project, *_ = ready(tmp_path)
    proposal = proposal_with_available_tests(project)
    imported = import_evidence_proposal(project, proposal)
    EvidenceClosureFinalizer().finalize(project, proposal=imported.occurrence)
    reopened = open_project(project.root)
    coverage = reopened.load_json_artifact(S.EVIDENCE_CLOSURE, A.EVIDENCE_COVERAGE_INVENTORY)
    test = next(f for f in coverage["facets"] if f["lane"] == "test")
    assert test["disposition"] == "EXPLICIT_GAP"
    assert test["material_ids"] and test["gap_ids"]
    gaps = reopened.load_json_artifact(S.EVIDENCE_CLOSURE, A.EVIDENCE_GAP_REGISTER)
    assert next(g for g in gaps["gaps"] if g["lane"] == "test")["basis"]


def test_transient_retrieval_reports_specific_facet_path_and_cannot_be_waived(tmp_path):
    from unittest.mock import patch

    from driver_port_factory.acquisition.facets import RetrievalOutcome
    from driver_port_factory.acquisition.retrieval import EvidenceRetriever
    from driver_port_factory.acquisition.retrieval_result import RetrievalFailure

    project, *_ = ready(tmp_path)
    proposal = proposal_with_available_tests(project)
    imported = import_evidence_proposal(project, proposal)
    retrieve = EvidenceRetriever.retrieve

    def failed(self, facet, locator, **kwargs):
        if facet.lane.value == "test":
            raise RetrievalFailure(RetrievalOutcome.FAILED, "retrieved material is empty")
        return retrieve(self, facet, locator, **kwargs)

    with (
        patch.object(EvidenceRetriever, "retrieve", failed),
        pytest.raises(WorkflowError) as caught,
    ):
        EvidenceClosureFinalizer().finalize(project, proposal=imported.occurrence)
    text = str(caught.value)
    assert "test/source_tests" in text and "drivers/example.c" in text
    assert "empty" in text and "cannot waive" in text
    assert project.stage(S.EVIDENCE_CLOSURE).status.value == "RUNNING"
    # Same proposal can succeed after the actual retrieval fault is gone.
    EvidenceClosureFinalizer().finalize(project, proposal=imported.occurrence)
    open_project(project.root).verify_integrity()


def test_coverage_limitation_requires_basis():
    with pytest.raises(WorkflowError, match="supply gap.basis"):
        gap_reason(parse_facet("test", "source_tests"), (), ())


def test_proposal_recovery_rejects_report_pass_in_same_turn_and_accepts_json(tmp_path):
    project, *_ = ready(tmp_path)
    project.start(S.EVIDENCE_CLOSURE)
    with pytest.raises(CheckerDecisionRequired):
        request_recovery(project, S.EVIDENCE_CLOSURE, WorkflowError("test/source_tests: empty"))
    report = project.root / "report.md"
    report.write_text("This prose cannot replace a proposal")
    job = str(uuid4())
    with pytest.raises(WorkflowError, match="no captured outputs"):
        write_submission(
            project,
            S.EVIDENCE_CLOSURE,
            job_id=job,
            file_path=str(report),
            kind="report",
            decision="pass",
        )
    proposal = project.root / "proposal.json"
    proposal.write_text('{"facets": []}')
    receipt = write_submission(
        project,
        S.EVIDENCE_CLOSURE,
        job_id=job,
        file_path=str(proposal),
        kind="proposal",
        decision="submit",
    )
    assert json.loads(receipt.read_text())["kind"] == "proposal"


def test_repeated_proposal_failure_pauses_across_restart_and_replay_is_not_a_new_repair(tmp_path):
    project, *_ = ready(tmp_path)
    project.start(S.EVIDENCE_CLOSURE)
    stage = S.EVIDENCE_CLOSURE

    def response(data):
        project.record_artifact(
            stage,
            GeneratedArtifact(
                CodexArtifact.JOB_RESULT, json.dumps(data).encode(), "synthetic:worker-proposal"
            ),
        )

    response({"facets": []})
    for _ in range(4):  # Replaying the same controller failure is one observation.
        with pytest.raises(CheckerDecisionRequired):
            request_recovery(project, stage, WorkflowError("same retrieval fault"))
        clear_pending(project, stage)
    response({"facets": []})
    with pytest.raises(CheckerDecisionRequired):
        request_recovery(project, stage, WorkflowError("same retrieval fault"))
    clear_pending(project, stage)
    # Replaying the second response must not become a third paid response.
    with pytest.raises(CheckerDecisionRequired):
        request_recovery(project, stage, WorkflowError("same retrieval fault"))
    clear_pending(project, stage)
    response({"facets": [], "rationale": "prose alone is not a repair"})
    with pytest.raises(RecoveryPaused):
        request_recovery(project, stage, WorkflowError("same retrieval fault"))
    project = open_project(project.root)
    with pytest.raises(RecoveryPaused):
        pending_decision(project, stage)
    response({"facets": [{"changed": "locator"}]})
    assert pending_decision(project, stage) is None
