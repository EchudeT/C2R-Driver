from pathlib import Path
from unittest.mock import patch

from driver_port_factory.codex.gateway import CodexResult
from driver_port_factory.composition import open_project
from driver_port_factory.migration.analysis_delivery import prepared
from driver_port_factory.migration.contracts import MigrationArtifact as A, MigrationStage as S
from driver_port_factory.target_study.contracts import TargetStudyArtifact as T, TargetStudyStage as TS
from tests.submission_support import submit
from tests.workflow_support import ready_implementation, runner


def test_combined_analysis_captures_contracts_without_second_model_call_and_repairs_narrowly(tmp_path):
    project = ready_implementation(tmp_path, reviewed=False)
    project.start(S.ANALYSIS_REVIEW)
    project.retry_from(TS.STUDY, trigger=S.ANALYSIS_REVIEW, reason="synthetic full analysis repair")
    port = runner(project)
    calls = []

    def gateway(job):
        calls.append(job.stage)
        report = job.execution_root / "analysis.md"
        report.write_text("Synthetic source/target mapping, contracts and assertion provenance.\n"
                          + ("Corrected contract only.\n" if job.stage is S.CONTRACTS else ""))
        submit(project, job, report, kind="report", decision="pass")
        return CodexResult(job.job_id, "", "analysis-worker")

    with patch("driver_port_factory.codex.cli.CodexExecGateway.run", side_effect=gateway):
        port._target_study(project)
        # A restart between acceptance and downstream capture retains the prepared receipt.
        project = open_project(project.root)
        assert prepared(project) is not None
        port._handoff(project)
        port._contracts(project)
        study = project.artifact(TS.STUDY, T.REPORT)
        assert project.artifact(S.CONTRACTS, A.CONTRACTS).digest == study.digest
        assert project.artifact(S.CONTRACTS, A.TEST_PORT_MATRIX).digest == study.digest
        assert calls == [TS.STUDY]
        assert project.stage(S.ANALYSIS_REVIEW).status.value == "READY"

        project.start(S.ANALYSIS_REVIEW)
        project.retry_from(S.CONTRACTS, trigger=S.ANALYSIS_REVIEW, reason="synthetic assertion defect")
        assert prepared(project) is None
        port._contracts(project)
        assert calls == [TS.STUDY, S.CONTRACTS]
        assert project.artifact(TS.STUDY, T.REPORT).digest == study.digest
        assert project.artifact(S.CONTRACTS, A.CONTRACTS).digest != study.digest
    project.verify_integrity()


def test_analysis_receipt_invalidates_changed_rules_and_source_materials(tmp_path):
    from driver_port_factory.migration.analysis_delivery import identity, record
    from driver_port_factory.target_study.reuse import identity as study_identity
    from driver_port_factory.knowledge.corpus import CorpusManifest
    from dataclasses import replace

    project = ready_implementation(tmp_path, reviewed=False)
    record(project)
    assert prepared(project) is not None
    rule = Path(project.config.skill_root) / "knowledge-guided-driver-port/references/translation.md"
    rule.write_text(rule.read_text() + "Changed source obligations.\n")
    assert prepared(project) is None
    # Combined analysis includes source conclusions, so target-only reuse is no longer sound.
    corpus = CorpusManifest.current(project)
    before = study_identity(project)
    records = tuple(replace(r, sha256="a" * 64) if r.facet.lane.value == "source" else r
                    for r in corpus.records)
    with patch.object(CorpusManifest, "current", return_value=replace(corpus, records=records)):
        assert study_identity(project) != before
    assert "materials_manifest" in identity(project)["inputs"]
