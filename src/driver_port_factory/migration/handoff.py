from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
from contextlib import closing
from typing import Any

from ..acquisition.contracts import AcquisitionArtifact, AcquisitionStage
from ..acquisition.facets import EvidenceLane
from ..acquisition.frozen_checkout_validation import verify_git_checkout, verify_lock
from ..acquisition.material import GitBlobOrigin
from ..acquisition.repository import load_repository_acquisition
from ..acquisition.repository_manifest import RepositoryAcquisition
from ..acquisition.repository_role import RepositoryRole
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
from ..environment.contracts import EnvironmentArtifact, EnvironmentStage
from ..environment.models import ExperimentReadiness
from ..evaluation.contracts import EvaluationArtifact, EvaluationStage
from ..intake.contracts import IntakeArtifact, IntakeStage
from ..knowledge.contracts import (
    KnowledgeArtifact,
    KnowledgeEvidenceStatus,
    KnowledgeIndexStatus,
    KnowledgeStage,
)
from ..knowledge.corpus import CorpusManifest
from ..target_study.contracts import (
    TargetStudyArtifact,
    TargetStudyOutcome,
    TargetStudyStage,
)
from .contracts import HandoffMode, MigrationArtifact, MigrationStage


class MigrationHandoff:
    def create(self, project: Project) -> dict[str, Any]:
        stage = project.stage(MigrationStage.HANDOFF)
        if stage.status is not StageStatus.READY:
            raise WorkflowError(f"migration_handoff is {stage.status.value}, not READY")
        project.start(MigrationStage.HANDOFF)
        acquisition = load_repository_acquisition(project)
        corpus = CorpusManifest.current(project)
        mode = project.load_json_artifact(
            EnvironmentStage.RECOVERY, EnvironmentArtifact.MODE_RECORD
        )
        route = project.load_json_artifact(
            EnvironmentStage.RECOVERY,
            EnvironmentArtifact.EXPERIMENT_ROUTE,
        )
        knowledge = project.load_json_artifact(
            KnowledgeStage.KNOWLEDGE_BASE, KnowledgeArtifact.QUERY_CONTRACT
        )
        probes = project.load_json_artifact(
            KnowledgeStage.KNOWLEDGE_BASE,
            KnowledgeArtifact.TARGET_PROBE_RESULTS,
        )
        change_plan = project.load_json_artifact(
            TargetStudyStage.STUDY, TargetStudyArtifact.CHANGE_PLAN
        )
        record = {
            "schema_version": 1,
            "status": StageStatus.READY.value,
            "workspace_root": str(project.root),
            "identity": self._identity(project),
            "repositories": self._repositories(acquisition),
            "evidence": self._evidence(project, corpus, probes),
            "environment": self._environment(project, mode, route),
            "knowledge": self._knowledge(project, knowledge),
            "target_study": self._target_study(project, change_plan),
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
    def _identity(project: Project) -> dict[str, Any]:
        return {
            "source_platform": project.config.source_platform,
            "target_platform": project.config.target_platform,
            "confirmed_driver_name": project.config.driver_name,
            "migration_envelope": project.load_json_artifact(
                IntakeStage.ENVELOPE_FREEZE, IntakeArtifact.MIGRATION_ENVELOPE
            ),
        }

    @staticmethod
    def _repositories(acquisition: RepositoryAcquisition) -> dict[str, Any]:
        return {
            "frozen": [record.to_dict() for record in acquisition.checkouts],
            "upstream_read_only_paths": [record.checkout_path for record in acquisition.checkouts],
            "writable_target_path": acquisition.target_worktree.path,
        }

    @staticmethod
    def _evidence(
        project: Project, corpus: CorpusManifest, probes: dict[str, Any]
    ) -> dict[str, Any]:
        source_paths, source_tests, qemu_model = [], [], []
        for record in corpus.records:
            origin = record.origin
            if isinstance(origin, GitBlobOrigin) and origin.repository is RepositoryRole.SOURCE:
                source_paths.append(origin.path)
                if record.facet.lane is EvidenceLane.TEST:
                    source_tests.append(origin.path)
            if record.facet.lane is EvidenceLane.QEMU:
                qemu_model.append(record.to_dict())
        gaps = project.load_json_artifact(
            AcquisitionStage.EVIDENCE_CLOSURE,
            AcquisitionArtifact.EVIDENCE_GAP_REGISTER,
        )["gaps"]
        return {
            "materials_manifest": {
                "path": corpus.source,
                "sha256": corpus.digest,
                "record_count": len(corpus.records),
            },
            "source_paths": sorted(set(source_paths)),
            "source_test_paths": sorted(set(source_tests)),
            "qemu_device_model": qemu_model,
            "known_gaps": gaps,
            "target_probe_gaps": [
                probe
                for probe in probes["probes"]
                if not probe["verification"].get("positive_evidence", True)
            ],
        }

    @staticmethod
    def _environment(
        project: Project, mode: dict[str, Any], route: dict[str, Any]
    ) -> dict[str, Any]:
        ready = project.artifact(
            EnvironmentStage.RECOVERY, EnvironmentArtifact.EXPERIMENT_READY_RUN
        )
        attempts = [
            ref.to_dict()
            for ref in project.artifact_refs(
                stage=EnvironmentStage.RECOVERY, direction=ArtifactDirection.OUTPUT
            )
            if ref.kind == EnvironmentArtifact.RECOVERY_ATTEMPT.value
        ]
        return {
            "artifact_mode": mode["artifact_mode"],
            "runtime_inputs": mode.get("runner_evidence", []),
            "driver_insertion_or_packaging_path": mode.get("driver_insertion_or_packaging_path"),
            "qemu_invocation_or_runner": route["command"],
            "experiment_ready_run": ready.to_dict(),
            "experiment_ready_evidence": route,
            "recovery_attempts": attempts,
            "remaining_external_blockers": [],
        }

    @staticmethod
    def _knowledge(project: Project, contract: dict[str, Any]) -> dict[str, Any]:
        skill = project.artifact(KnowledgeStage.KNOWLEDGE_BASE, KnowledgeArtifact.GENERATED_SKILL)
        return {
            "skill_name": contract["generated_skill_name"],
            "skill_path": contract["generated_skill_path"],
            "skill_sha256": skill.digest,
            "status_result": contract["index_status"],
            "commands": contract["commands"],
        }

    @staticmethod
    def _target_study(project: Project, change_plan: dict[str, Any]) -> dict[str, Any]:
        refs = {
            artifact.value: project.artifact(TargetStudyStage.STUDY, artifact).to_dict()
            for artifact in (
                TargetStudyArtifact.PROFILE,
                TargetStudyArtifact.STRUCTURED_PROFILE,
                TargetStudyArtifact.API_EVIDENCE,
                TargetStudyArtifact.ANALOGOUS_DRIVER_TRACE,
            )
        }
        return {
            "artifacts": refs,
            "integration_path": change_plan["integration_path"],
            "required_change_level": change_plan["required_change_level"],
            "initial_driver_owned_paths": change_plan["driver_owned_paths"],
            "proposed_preexisting_target_changes": change_plan["proposed_preexisting_changes"],
        }

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
        public = project.load_json_artifact(
            EvaluationStage.BLIND_BINDING, EvaluationArtifact.PUBLIC_BUNDLE
        )
        commitment = project.load_json_artifact(
            EvaluationStage.BLIND_BINDING, EvaluationArtifact.CURATOR_COMMITMENT
        )
        return {
            "mode": HandoffMode.BLIND_CANDIDATE.value,
            "private_evaluator_material_access": False,
            "public_bundle": public,
            "curator_commitment": commitment,
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
        refs = [
            ref
            for stage in project.workflow.spec(MigrationStage.HANDOFF).dependencies
            for ref in project.artifact_refs(stage=stage, direction=ArtifactDirection.OUTPUT)
        ]
        return [ref.to_dict() for ref in sorted(refs, key=_ref_key)]


def validate_handoff_bundle(context: BundleValidationContext) -> None:
    _, data = context.one_current(MigrationArtifact.HANDOFF)
    record = json_object(data, MigrationArtifact.HANDOFF.value)
    expected = [ref.to_dict() for ref, _ in sorted(context.dependency_artifacts, key=_payload_key)]
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
    _validate_evaluation(record.get("evaluation"))


def _validate_evaluation(value: object) -> None:
    if not isinstance(value, dict):
        raise WorkflowError("migration handoff evaluation mode is missing")
    mode = HandoffMode(value.get("mode"))
    if mode is HandoffMode.DEVELOPER:
        if set(value) != {"mode"}:
            raise WorkflowError("developer handoff must not contain blind fields")
        return
    required = {"mode", "private_evaluator_material_access", "public_bundle", "curator_commitment"}
    if set(value) != required or value["private_evaluator_material_access"] is not False:
        raise WorkflowError("blind handoff must bind public inputs and deny private access")


def _ref_key(ref: ArtifactRef) -> tuple[str, int, str]:
    return ref.kind, ref.ordinal if ref.ordinal is not None else -1, ref.digest


def _payload_key(payload: tuple[ArtifactRef, bytes]) -> tuple[str, int, str]:
    return _ref_key(payload[0])
