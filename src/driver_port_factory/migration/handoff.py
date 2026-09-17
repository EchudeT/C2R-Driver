from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
from contextlib import closing
from typing import Any

from ..acquisition.contracts import AcquisitionArtifact
from ..acquisition.frozen_checkout_validation import verify_git_checkout, verify_lock
from ..acquisition.repository import load_repository_acquisition
from ..acquisition.repository_manifest import RepositoryAcquisition
from ..core.models import (
    ArtifactDirection,
    ArtifactRef,
    EvaluationMode,
    GeneratedArtifact,
    StageStatus,
    WorkflowError,
)
from ..core.project import Project
from ..core.validation import BundleValidationContext, json_object
from ..environment.contracts import EnvironmentArtifact
from ..environment.models import ExperimentReadiness
from ..evaluation.contracts import EvaluationArtifact
from ..intake.contracts import IntakeArtifact
from ..knowledge.contracts import (
    KnowledgeArtifact,
    KnowledgeEvidenceStatus,
    KnowledgeIndexStatus,
)
from ..target_study.contracts import (
    TargetStudyArtifact,
    TargetStudyOutcome,
)
from .contracts import HandoffMode, MigrationArtifact, MigrationStage

_HANDOFF_ARTIFACTS = (
    IntakeArtifact.MIGRATION_ENVELOPE,
    AcquisitionArtifact.REPOSITORY_MANIFEST,
    AcquisitionArtifact.MATERIALS_MANIFEST,
    AcquisitionArtifact.EVIDENCE_GAP_REGISTER,
    EnvironmentArtifact.MODE_RECORD,
    EnvironmentArtifact.EXPERIMENT_READY_RUN,
    EnvironmentArtifact.EXPERIMENT_ROUTE,
    EnvironmentArtifact.RECOVERY_ATTEMPT,
    KnowledgeArtifact.STATUS,
    KnowledgeArtifact.QUERY_CONTRACT,
    KnowledgeArtifact.GENERATED_SKILL,
    KnowledgeArtifact.TARGET_PROBE_RESULTS,
    TargetStudyArtifact.STRUCTURED_PROFILE,
    TargetStudyArtifact.API_EVIDENCE,
    TargetStudyArtifact.ANALOGOUS_DRIVER_TRACE,
    TargetStudyArtifact.CHANGE_PLAN,
    TargetStudyArtifact.REPORT,
)
_BLIND_HANDOFF_ARTIFACTS = (
    EvaluationArtifact.PUBLIC_BUNDLE,
    EvaluationArtifact.CURATOR_COMMITMENT,
)


class MigrationHandoff:
    def create(self, project: Project) -> dict[str, Any]:
        stage = project.stage(MigrationStage.HANDOFF)
        if stage.status is StageStatus.READY:
            project.start(MigrationStage.HANDOFF)
        elif stage.status is not StageStatus.RUNNING:
            raise WorkflowError(f"migration_handoff is {stage.status.value}, not READY")
        acquisition = load_repository_acquisition(project)
        record = {
            "schema_version": 2,
            "status": StageStatus.READY.value,
            "source_platform": project.config.source_platform,
            "target_platform": project.config.target_platform,
            "confirmed_driver_name": project.config.driver_name,
            "downstream_skill": "knowledge-guided-driver-port",
            "workspace_root": str(project.root),
            "local_git_state": self._git_state(project, acquisition.target_worktree.path),
            "evaluation": self._evaluation(project),
            "ledger": self._ledger(project),
            "upstream_artifacts": self._dependency_refs(project),
        }
        project.finalize_stage(
            MigrationStage.HANDOFF,
            (
                GeneratedArtifact(
                    MigrationArtifact.HANDOFF,
                    (
                        json.dumps(record, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
                    ).encode(),
                    "generated:migration-handoff",
                ),
            ),
        )
        return record

    @staticmethod
    def _git_state(project: Project, relative: str) -> dict[str, Any]:
        root = (project.root / relative).resolve()
        head = subprocess.run(
            ("git", "-C", str(root), "rev-parse", "HEAD"),
            check=True,
            capture_output=True,
        ).stdout.strip()
        status = subprocess.run(
            ("git", "-C", str(root), "status", "--porcelain=v1", "-z"),
            check=True,
            capture_output=True,
        ).stdout
        return {
            "worktree": relative,
            "head": head.decode(),
            "status_sha256": hashlib.sha256(status).hexdigest(),
            "dirty": bool(status),
        }

    @staticmethod
    def _evaluation(project: Project) -> dict[str, Any]:
        if project.config.evaluation_mode is not EvaluationMode.PROSPECTIVE_BLIND:
            return {"mode": HandoffMode.DEVELOPER.value}
        return {
            "mode": HandoffMode.BLIND_CANDIDATE.value,
            "private_evaluator_material_access": False,
        }

    @staticmethod
    def _ledger(project: Project) -> dict[str, str]:
        with closing(sqlite3.connect(project.database_path)) as connection:
            row = connection.execute(
                "SELECT event_hash FROM events ORDER BY sequence DESC LIMIT 1"
            ).fetchone()
        return {"path": str(project.database_path), "previous_digest": row[0]}

    @staticmethod
    def _dependency_refs(project: Project) -> list[dict[str, Any]]:
        artifacts = _HANDOFF_ARTIFACTS
        if project.config.evaluation_mode is EvaluationMode.PROSPECTIVE_BLIND:
            artifacts += _BLIND_HANDOFF_ARTIFACTS
        kinds = {artifact.value for artifact in artifacts}
        refs = [
            ref
            for stage in project.workflow.spec(MigrationStage.HANDOFF).dependencies
            for ref in project.current_artifact_refs(
                stage=stage, direction=ArtifactDirection.OUTPUT
            )
            if ref.kind in kinds
        ]
        return [
            {
                **ref.to_dict(),
                "path": str(project.artifacts.path_for_digest(ref.digest)),
            }
            for ref in sorted(refs, key=_ref_key)
        ]


def validate_handoff_bundle(context: BundleValidationContext) -> None:
    _, data = context.one_current(MigrationArtifact.HANDOFF)
    record = json_object(data, MigrationArtifact.HANDOFF.value)
    required = {
        "schema_version",
        "status",
        "source_platform",
        "target_platform",
        "confirmed_driver_name",
        "downstream_skill",
        "workspace_root",
        "local_git_state",
        "evaluation",
        "ledger",
        "upstream_artifacts",
    }
    if (
        set(record) != required
        or record["schema_version"] != 2
        or record["status"] != StageStatus.READY.value
        or record["downstream_skill"] != "knowledge-guided-driver-port"
    ):
        raise WorkflowError("migration handoff has an invalid schema")
    evaluation = record["evaluation"]
    _validate_evaluation(evaluation)
    artifacts = _HANDOFF_ARTIFACTS
    if evaluation["mode"] == HandoffMode.BLIND_CANDIDATE.value:
        artifacts += _BLIND_HANDOFF_ARTIFACTS
    kinds = {artifact.value for artifact in artifacts}
    expected = [
        {
            **ref.to_dict(),
            "path": str(
                (
                    context.project_root
                    / Project.CONTROL_DIR
                    / "cas"
                    / ref.cas_path
                ).resolve()
            ),
        }
        for ref, _ in sorted(context.dependency_artifacts, key=_payload_key)
        if ref.kind in kinds
    ]
    if record.get("upstream_artifacts") != expected:
        raise WorkflowError("migration handoff does not bind every upstream artifact")
    repositories = RepositoryAcquisition.from_dict(
        json.loads(context.one_dependency(AcquisitionArtifact.REPOSITORY_MANIFEST)[1])
    )
    for checkout in repositories.checkouts:
        verify_lock(context.project_root, checkout)
        verify_git_checkout(context.project_root, checkout)
    ready = json_object(
        context.one_dependency(EnvironmentArtifact.EXPERIMENT_READY_RUN)[1],
        EnvironmentArtifact.EXPERIMENT_READY_RUN.value,
    )
    knowledge = json_object(
        context.one_dependency(KnowledgeArtifact.STATUS)[1], KnowledgeArtifact.STATUS.value
    )
    probes = json_object(
        context.one_dependency(KnowledgeArtifact.TARGET_PROBE_RESULTS)[1],
        KnowledgeArtifact.TARGET_PROBE_RESULTS.value,
    )
    target = json_object(
        context.one_dependency(TargetStudyArtifact.REPORT)[1], TargetStudyArtifact.REPORT.value
    )
    if (
        ExperimentReadiness(ready.get("readiness")) is not ExperimentReadiness.PASS
        or KnowledgeIndexStatus(knowledge.get("status")) is not KnowledgeIndexStatus.READY
        or KnowledgeEvidenceStatus(probes.get("status")) is not KnowledgeEvidenceStatus.PASS
        or TargetStudyOutcome(target.get("status")) is not TargetStudyOutcome.PASS
    ):
        raise WorkflowError("migration handoff upstream readiness is incomplete")


def _validate_evaluation(value: object) -> None:
    if not isinstance(value, dict):
        raise WorkflowError("migration handoff evaluation mode is missing")
    mode = HandoffMode(value.get("mode"))
    if mode is HandoffMode.DEVELOPER:
        if set(value) != {"mode"}:
            raise WorkflowError("developer handoff must not contain blind fields")
        return
    required = {"mode", "private_evaluator_material_access"}
    if set(value) != required or value["private_evaluator_material_access"] is not False:
        raise WorkflowError("blind handoff must bind public inputs and deny private access")


def _ref_key(ref: ArtifactRef) -> tuple[str, int, str]:
    return ref.kind, ref.ordinal if ref.ordinal is not None else -1, ref.digest


def _payload_key(payload: tuple[ArtifactRef, bytes]) -> tuple[str, int, str]:
    return _ref_key(payload[0])
