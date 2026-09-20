"""Exercise the real source controller operation, not a prompt-wording snapshot."""
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from driver_port_factory.codex.contracts import CodexContinuation
from driver_port_factory.composition import open_project
from driver_port_factory.core.models import StageStatus, WorkflowError
from driver_port_factory.migration.contracts import MigrationStage as M
from driver_port_factory.orchestration.protocol import operation
from driver_port_factory.source_analysis.contracts import SourceAnalysisStage as S
from driver_port_factory.source_analysis.query import query_symbols
from tests.test_source_closure import ready_project, source_closure
from tests.test_workflow_alignment import runner


def test_source_operation_resume_read_and_complete_without_prerequisite_rollback(tmp_path):
    project, checkouts = ready_project(tmp_path)
    closure = source_closure(project, checkouts)
    root = project.root / "work/stage-work/source_closure"
    root.mkdir(parents=True)
    database = root / "compile_commands.json"
    database.write_text(json.dumps([{
        "file": str(Path(closure["source_root"]) / u["source_path"]),
        "directory": u["compile_directory"], "arguments": u["arguments"],
    } for u in closure["translation_units"]]))
    report = root / "source.md"
    report.write_text("DPF_RUN: SOURCE_ANALYSIS\n")
    port = runner(project)
    with patch.object(port, "_materialize_codex_report", return_value=report):
        with pytest.raises(CodexContinuation):
            port._accept_source_closure_result(project, None)
    assert project.stage(S.SOURCE_CLOSURE).status is StageStatus.RUNNING
    assert query_symbols(project, symbols=["example_init"])[0]["match_count"] > 0
    project = open_project(project.root)
    port = runner(project)
    with patch.object(port, "_materialize_codex_report", return_value=report), patch(
        "driver_port_factory.port.StructuredCAnalysisService.analyze",
        side_effect=AssertionError("unchanged receipt must be reused")):
        with pytest.raises(CodexContinuation):
            port._accept_source_closure_result(project, None)
        report.write_text("# Coverage\nRead compiler facts and originals.\nDPF_SELF_REVIEW: PASS\n")
        port._accept_source_closure_result(project, None)
    assert project.stage(S.SOURCE_CLOSURE).status is StageStatus.PASS
    assert project.stage(M.CONTRACTS).status is StageStatus.READY
    project.verify_integrity()


def test_operation_is_stage_scoped():
    assert operation("source_closure", "# Inputs\nDPF_RUN: SOURCE_ANALYSIS\n") == "SOURCE_ANALYSIS"
    with pytest.raises(WorkflowError):
        operation("source_closure", "# Inputs\nDPF_RUN: PUBLIC_QEMU\n")


def test_evidence_repair_preserves_environment(tmp_path):
    from driver_port_factory.acquisition.contracts import AcquisitionStage as A
    from driver_port_factory.environment.contracts import EnvironmentStage as E
    project, _ = ready_project(tmp_path)
    before = project.current_artifact_refs(stage=E.RECOVERY)
    project.start(S.SOURCE_CLOSURE)
    project.retry_from(A.EVIDENCE_CLOSURE, trigger=S.SOURCE_CLOSURE, reason="missing original")
    assert project.stage(E.RECOVERY).status is StageStatus.PASS
    assert project.current_artifact_refs(stage=E.RECOVERY) == before
    project.verify_integrity()


def test_target_study_reuse_is_bound_to_non_source_evidence(tmp_path):
    from driver_port_factory.target_study.reuse import identity, remember, restore
    from driver_port_factory.target_study.contracts import TargetStudyStage as T
    from driver_port_factory.knowledge.corpus import CorpusManifest
    from dataclasses import replace

    from tests.test_workflow_alignment import ready_implementation
    project = ready_implementation(tmp_path)
    original = identity(project)
    corpus = CorpusManifest.current(project)
    source = next(r for r in corpus.records if r.facet.lane.value == "source")
    target = next(r for r in corpus.records if r.facet.lane.value == "target")
    with patch.object(CorpusManifest, "current", return_value=replace(corpus,
            records=(*corpus.records, source))):
        assert identity(project) == original
    with patch.object(CorpusManifest, "current", return_value=replace(corpus,
            records=(*corpus.records, target))):
        assert identity(project) != original
    remember(project)
    project.start(M.DRIVER_IMPLEMENTATION)
    project.retry_from(T.STUDY, trigger=M.DRIVER_IMPLEMENTATION, reason="fixture invalidation")
    assert project.stage(M.DRIVER_IMPLEMENTATION).status is StageStatus.WAITING_FOR_USER
    assert project.stage(T.STUDY).status is StageStatus.PASS
    assert project.stage(S.SOURCE_CLOSURE).status is StageStatus.PASS
    project.verify_integrity()


def test_upgrade_preserves_idle_run_and_retires_only_unused_checkpoint(tmp_path):
    import sqlite3
    from driver_port_factory.control.protocol_upgrade import upgrade
    from driver_port_factory.core.events import StageEvent
    from driver_port_factory.core.ledger import append_event

    project, _ = ready_project(tmp_path)
    with sqlite3.connect(project.database_path) as db:
        db.row_factory = sqlite3.Row
        # Model the old, never-executed static checkpoint at the end. The upgrade
        # maps current positions from the new definition, not historical numbers.
        db.execute("""INSERT INTO stages SELECT 'structured_c_analysis',99,
            description,owner,dependencies,required_outputs,auxiliary_outputs,
            allowed_roles,accept_failed_dependencies,'PENDING',NULL,NULL,NULL
            FROM stages WHERE name='source_closure'""")
        append_event(db, StageEvent.RETRIED, {
            "stage": "structured_c_analysis", "trigger": "source_closure",
            "status": "PENDING", "artifact_boundaries": {"input": 0, "output": 0}})
    result = upgrade(project.root)
    assert result["source_stage"] == "READY"
    upgraded = open_project(project.root)
    assert "structured_c_analysis" not in upgraded.workflow.stage_values
    assert (project.control / "pre-protocol-upgrade.sqlite3").is_file()
    upgraded.verify_integrity()
    with pytest.raises(WorkflowError, match="already"):
        upgrade(project.root)
