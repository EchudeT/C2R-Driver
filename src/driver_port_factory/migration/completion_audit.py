from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import Counter
from pathlib import Path
from typing import Any

from ..acquisition.repository import load_repository_acquisition
from ..core.ledger import canonical_json
from ..core.models import (
    ActorRole,
    ArtifactDirection,
    EvaluationMode,
    GeneratedArtifact,
    StageStatus,
    WorkflowError,
)
from ..core.project import Project
from ..core.validation import BundleValidationContext, json_object
from ..knowledge.contracts import KnowledgeEvidenceStatus
from ..sealing.contracts import PrivateEvaluationState, SealingArtifact, SealingStage
from .artifact_preparation import ArtifactPreparationService
from .contracts import (
    ContractEvidenceStatus,
    ContractExecutionStatus,
    MigrationArtifact,
    MigrationStage,
    PublicRunAttribution,
    TestDisposition,
)

AUDIT_STATUS = "RECORDED"


class CompletionAuditService:
    def run(self, project: Project) -> dict[str, Any]:
        project.ensure_role(ActorRole.DEVELOPER, ActorRole.MIGRATION_OPERATOR)
        project.verify_integrity()
        stage = project.stage(MigrationStage.COMPLETION_AUDIT)
        if stage.status is StageStatus.PASS:
            return project.load_json_artifact(
                MigrationStage.COMPLETION_AUDIT, MigrationArtifact.EVIDENCE_AUDIT
            )
        if stage.status is StageStatus.READY:
            project.start(MigrationStage.COMPLETION_AUDIT)
        elif stage.status is not StageStatus.RUNNING:
            raise WorkflowError(f"completion_audit is {stage.status.value}, not READY/RUNNING")

        stages, artifacts = _snapshot(project)
        contracts = self._document(project, MigrationStage.CONTRACTS, MigrationArtifact.CONTRACTS)
        tests = self._document(
            project, MigrationStage.TEST_ADAPTATION, MigrationArtifact.TEST_PORT_MATRIX
        )
        implementation = self._document(
            project,
            MigrationStage.DRIVER_IMPLEMENTATION,
            MigrationArtifact.IMPLEMENTATION_BUNDLE,
        )
        changes = self._document(
            project,
            MigrationStage.DRIVER_IMPLEMENTATION,
            MigrationArtifact.TARGET_CHANGE_INVENTORY,
        )
        compliance = self._document(
            project, MigrationStage.TARGET_COMPLIANCE, MigrationArtifact.COMPLIANCE_REPORT
        )
        identity = self._document(
            project, MigrationStage.ARTIFACT_PREPARATION, MigrationArtifact.ARTIFACT_IDENTITY
        )
        public = self._optional_document(
            project, MigrationStage.PUBLIC_QEMU_VALIDATION, MigrationArtifact.PUBLIC_QEMU_REPORT
        )
        repair = self._document(
            project, MigrationStage.PUBLIC_REPAIR, MigrationArtifact.PUBLIC_REPAIR_REPORT
        )

        runs = (public or {}).get("runs", [])
        target_driver_ran = (
            public is not None
            and public.get("integration_boundary") is None
            and any(
                run.get("execution_status") == ContractExecutionStatus.PASS.value
                and run.get("attribution")
                == PublicRunAttribution.TARGET_DRIVER_ON_QEMU.value
                for run in runs
            )
        )
        contract_results = _contract_results(contracts, runs)
        test_results = _test_results(tests, runs)
        lineage = self._lineage(project, implementation, compliance, identity, public)
        blind = self._blind_candidate(project)
        unresolved = [
            {"kind": "contract", "id": item["id"], "status": item["execution_status"]}
            for item in contract_results
            if item["execution_status"]
            not in {
                ContractExecutionStatus.PASS.value,
                ContractExecutionStatus.NOT_APPLICABLE.value,
            }
        ] + [
            {"kind": "test", "id": item["test_id"], "status": item["execution_status"]}
            for item in test_results
            if item["execution_status"]
            not in {
                ContractExecutionStatus.PASS.value,
                ContractExecutionStatus.NOT_APPLICABLE.value,
            }
        ]
        snapshot_digest = hashlib.sha256(_json(artifacts)).hexdigest()
        audit = {
            "schema_version": 1,
            "audit_status": AUDIT_STATUS,
            "evaluation_mode": project.config.evaluation_mode.value,
            "stage_results": stages,
            "artifact_snapshot": artifacts,
            "artifact_snapshot_sha256": snapshot_digest,
            "contract_results": contract_results,
            "test_results": test_results,
            "source_and_translation_coverage": {
                "translation_coverage": _reference(
                    project,
                    MigrationStage.DRIVER_IMPLEMENTATION,
                    MigrationArtifact.TRANSLATION_COVERAGE,
                ),
                "implementation": _reference(
                    project,
                    MigrationStage.DRIVER_IMPLEMENTATION,
                    MigrationArtifact.IMPLEMENTATION_BUNDLE,
                ),
            },
            "target_changes": changes.get("target_changes", []),
            "compliance": {
                "artifact": _reference(
                    project, MigrationStage.TARGET_COMPLIANCE, MigrationArtifact.COMPLIANCE_REPORT
                ),
                "status": compliance.get("status"),
                "evidence": compliance.get("evidence", []),
                "findings": compliance.get("findings", []),
            },
            "artifact_lineage": lineage,
            "public_runs": runs,
            "failure_attribution": _failure_attribution(public, repair),
            "unresolved": unresolved,
            "blind_candidate": blind,
            "scope_limits": {
                "qemu_evidence": (
                    PublicRunAttribution.TARGET_DRIVER_ON_QEMU.value
                    if target_driver_ran
                    else "QEMU_MODEL_ONLY"
                ),
                "integration_boundary": (public or {}).get("integration_boundary"),
                "real_hardware": KnowledgeEvidenceStatus.NOT_RUN.value,
                "private_evaluation": (
                    PrivateEvaluationState.NOT_RUN_BY_MIGRATOR.value
                    if blind is not None
                    else ContractExecutionStatus.NOT_APPLICABLE.value
                ),
            },
            "summary": {
                "execution_status_counts": dict(
                    sorted(Counter(item["execution_status"] for item in contract_results).items())
                ),
                "test_status_counts": dict(
                    sorted(Counter(item["execution_status"] for item in test_results).items())
                ),
                "unresolved_count": len(unresolved),
            },
        }
        data = _json(audit)
        project.finalize_stage(
            MigrationStage.COMPLETION_AUDIT,
            (
                GeneratedArtifact(
                    MigrationArtifact.EVIDENCE_AUDIT,
                    data,
                    f"generated:completion-audit:{hashlib.sha256(data).hexdigest()}",
                ),
            ),
        )
        return audit

    @staticmethod
    def _document(project: Project, stage: object, kind: object) -> dict[str, Any]:
        return project.load_json_artifact(stage, kind)

    @staticmethod
    def _optional_document(project: Project, stage: object, kind: object) -> dict[str, Any] | None:
        refs = [ref for ref in project.current_artifact_refs(stage=stage) if ref.kind == kind.value]
        if not refs:
            return None
        if len(refs) != 1:
            raise WorkflowError(f"completion audit expected one {kind.value}")
        value = json.loads(project.artifacts.read(refs[0]))
        if not isinstance(value, dict):
            raise WorkflowError(f"completion audit {kind.value} is not an object")
        return value

    @staticmethod
    def _lineage(
        project: Project,
        implementation: dict[str, Any],
        compliance: dict[str, Any],
        identity: dict[str, Any],
        public: dict[str, Any] | None,
    ) -> dict[str, Any]:
        implementation_ref = project.artifact(
            MigrationStage.DRIVER_IMPLEMENTATION, MigrationArtifact.IMPLEMENTATION_BUNDLE
        )
        effective_digest = implementation_ref.digest
        acquisition = load_repository_acquisition(project)
        worktree = (project.root / acquisition.target_worktree.path).resolve()
        ArtifactPreparationService._implementation_matches(worktree, implementation)
        compliance_digest = (
            compliance.get("inputs", {})
            .get(MigrationArtifact.IMPLEMENTATION_BUNDLE.value, {})
            .get("digest")
        )
        identity_digest = (
            identity.get("inputs", {})
            .get(MigrationArtifact.IMPLEMENTATION_BUNDLE.value, {})
            .get("digest")
        )
        runtime_ref = project.artifact(
            MigrationStage.ARTIFACT_PREPARATION, MigrationArtifact.RUNTIME_ARTIFACT
        )
        runtime_digest = identity.get("runtime_artifact", {}).get("sha256")
        if (
            effective_digest != compliance_digest
            or effective_digest != identity_digest
            or runtime_digest != runtime_ref.digest
        ):
            raise WorkflowError("completion audit found stale compliance or artifact lineage")
        qemu_binding = None
        if public is not None:
            qemu_inputs = public.get("inputs", {})
            if (
                qemu_inputs.get(MigrationArtifact.RUNTIME_ARTIFACT.value, {}).get("digest")
                != runtime_digest
                or qemu_inputs.get(MigrationArtifact.ARTIFACT_IDENTITY.value, {}).get("digest")
                != project.artifact(
                    MigrationStage.ARTIFACT_PREPARATION,
                    MigrationArtifact.ARTIFACT_IDENTITY,
                ).digest
            ):
                raise WorkflowError("completion audit found stale public QEMU lineage")
            qemu_binding = {
                "implementation_sha256": effective_digest,
                "artifact_sha256": runtime_digest,
            }
        return {
            "verified": True,
            "implementation_sha256": effective_digest,
            "compliance_sha256": project.artifact(
                MigrationStage.TARGET_COMPLIANCE, MigrationArtifact.COMPLIANCE_REPORT
            ).digest,
            "artifact_identity_sha256": project.artifact(
                MigrationStage.ARTIFACT_PREPARATION, MigrationArtifact.ARTIFACT_IDENTITY
            ).digest,
            "runtime_artifact_sha256": runtime_digest,
            "driver_presence": identity.get("driver_presence"),
            "artifact_mode": identity.get("artifact_mode"),
            "base_artifact": identity.get("base_artifact"),
            "public_qemu_binding": qemu_binding,
        }

    @staticmethod
    def _blind_candidate(project: Project) -> dict[str, Any] | None:
        if project.config.evaluation_mode is EvaluationMode.DEVELOPER_EVIDENCE:
            return None
        manifest = project.load_json_artifact(
            SealingStage.CANDIDATE_SEALING, SealingArtifact.CANDIDATE_MANIFEST
        )
        bundle = project.artifact(SealingStage.CANDIDATE_SEALING, SealingArtifact.CANDIDATE_BUNDLE)
        if hashlib.sha256(project.artifacts.read(bundle)).hexdigest() != manifest.get(
            "candidate_sha256"
        ):
            raise WorkflowError("completion audit candidate digest is detached")
        result = {
            "candidate_sha256": bundle.digest,
            "manifest": _reference(
                project, SealingStage.CANDIDATE_SEALING, SealingArtifact.CANDIDATE_MANIFEST
            ),
            "bundle": bundle.to_dict(),
            "private_evaluation": PrivateEvaluationState.NOT_RUN_BY_MIGRATOR.value,
        }
        if project.config.evaluation_mode is EvaluationMode.PROSPECTIVE_BLIND:
            receipt = project.load_json_artifact(
                SealingStage.CANDIDATE_TRANSFER, SealingArtifact.EVALUATOR_RECEIPT
            )
            transfer = project.load_json_artifact(
                SealingStage.CANDIDATE_TRANSFER, SealingArtifact.CANDIDATE_TRANSFER_RECORD
            )
            if (
                receipt.get("candidate_sha256") != bundle.digest
                or transfer.get("candidate_sha256") != bundle.digest
            ):
                raise WorkflowError("completion audit evaluator transfer is detached")
            result["evaluator_receipt"] = _reference(
                project, SealingStage.CANDIDATE_TRANSFER, SealingArtifact.EVALUATOR_RECEIPT
            )
            result["transfer"] = _reference(
                project,
                SealingStage.CANDIDATE_TRANSFER,
                SealingArtifact.CANDIDATE_TRANSFER_RECORD,
            )
        return result


def validate_completion_audit_bundle(context: BundleValidationContext) -> None:
    audit = json_object(
        context.one_current(MigrationArtifact.EVIDENCE_AUDIT)[1],
        MigrationArtifact.EVIDENCE_AUDIT.value,
    )
    expected_stages, expected_artifacts = _database_snapshot(context.project_root)
    if (
        audit.get("schema_version") != 1
        or audit.get("audit_status") != AUDIT_STATUS
        or audit.get("stage_results") != expected_stages
        or audit.get("artifact_snapshot") != expected_artifacts
        or audit.get("artifact_snapshot_sha256")
        != hashlib.sha256(_json(expected_artifacts)).hexdigest()
        or audit.get("artifact_lineage", {}).get("verified") is not True
    ):
        raise WorkflowError("completion audit is incomplete or detached from frozen evidence")
    for field, key in (("contract_results", "id"), ("test_results", "test_id")):
        results = audit.get(field)
        if not isinstance(results, list) or len({item.get(key) for item in results}) != len(
            results
        ):
            raise WorkflowError("completion audit result identities are incomplete")
        for item in results:
            ContractExecutionStatus(item.get("execution_status"))


def _snapshot(project: Project) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    audit_position = project.stage(MigrationStage.COMPLETION_AUDIT).position
    stages = []
    artifacts = []
    for stage in project.stages():
        if stage.position >= audit_position:
            continue
        stages.append({"stage": stage.name.value, "status": stage.status.value})
        artifacts.extend(
            {"stage": stage.name.value, **ref.to_dict()}
            for ref in project.artifact_refs(stage=stage.name, direction=ArtifactDirection.OUTPUT)
        )
    return stages, artifacts


def _database_snapshot(root: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    connection = sqlite3.connect(root / ".dpf" / "run.sqlite3")
    connection.row_factory = sqlite3.Row
    try:
        stages = [
            {"stage": row["name"], "status": row["status"]}
            for row in connection.execute(
                "SELECT name, status FROM stages WHERE position < "
                "(SELECT position FROM stages WHERE name = ?) ORDER BY position",
                (MigrationStage.COMPLETION_AUDIT.value,),
            )
        ]
        artifacts = [
            {
                "stage": row["stage_name"],
                "digest": row["digest"],
                "kind": row["kind"],
                "size": row["size"],
                "cas_path": row["cas_path"],
                "source": row["source"],
                "ordinal": row["ordinal"],
            }
            for row in connection.execute(
                """
                SELECT sa.stage_name, sa.digest, sa.kind, ac.size, ac.cas_path,
                       sa.source, sa.ordinal
                FROM stage_artifacts sa
                JOIN artifact_contents ac
                  ON ac.digest = sa.digest AND ac.kind = sa.kind
                JOIN stages st ON st.name = sa.stage_name
                WHERE sa.direction = ? AND st.position <
                      (SELECT position FROM stages WHERE name = ?)
                ORDER BY st.position, sa.ordinal
                """,
                (ArtifactDirection.OUTPUT.value, MigrationStage.COMPLETION_AUDIT.value),
            )
        ]
        return stages, artifacts
    finally:
        connection.close()


def _contract_results(document: dict[str, Any], runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "id": item["id"],
            "evidence_status": item["evidence_status"],
            "execution_status": _execution_status(item["execution_status"], observations),
            "observations": observations,
        }
        for item in document["contracts"]
        for observations in [
            [_run_observation(run) for run in runs if item["id"] in run.get("contract_ids", [])]
        ]
    ]


def _test_results(document: dict[str, Any], runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "test_id": item["test_id"],
            "evidence_status": (
                ContractEvidenceStatus.VERIFIED.value
                if observations
                and all(
                    observation["evidence_status"] == ContractEvidenceStatus.VERIFIED.value
                    for observation in observations
                )
                else KnowledgeEvidenceStatus.NOT_APPLICABLE.value
                if item["disposition"] == TestDisposition.EXCLUDE.value
                else KnowledgeEvidenceStatus.PLANNED.value
            ),
            "execution_status": _execution_status(item["execution_status"], observations),
            "observations": observations,
        }
        for item in document["tests"]
        for observations in [
            [_run_observation(run) for run in runs if item["test_id"] in run.get("test_ids", [])]
        ]
    ]


def _run_observation(run: dict[str, Any]) -> dict[str, Any]:
    return {
        "run_id": run.get("run_id"),
        "evidence_status": run.get("evidence_status", ContractEvidenceStatus.UNKNOWN.value),
        "execution_status": run.get("execution_status", ContractExecutionStatus.NOT_RUN.value),
        "attribution": run.get("attribution"),
    }


def _execution_status(default: str, observations: list[dict[str, Any]]) -> str:
    if not observations:
        return ContractExecutionStatus(default).value
    statuses = {ContractExecutionStatus(item["execution_status"]) for item in observations}
    for status in (
        ContractExecutionStatus.FAIL,
        ContractExecutionStatus.BLOCKED,
        ContractExecutionStatus.NOT_RUN,
        ContractExecutionStatus.PASS,
        ContractExecutionStatus.NOT_APPLICABLE,
    ):
        if status in statuses:
            return status.value
    raise AssertionError("unreachable execution status")


def _failure_attribution(
    public: dict[str, Any] | None, repair: dict[str, Any]
) -> list[dict[str, Any]]:
    failures = [
        {
            "run_id": run.get("run_id"),
            "status": run.get("execution_status"),
            "attribution": run.get("attribution"),
        }
        for run in (public or {}).get("runs", [])
        if run.get("execution_status") != ContractExecutionStatus.PASS.value
    ]
    if repair.get("outcome") != ContractExecutionStatus.NOT_APPLICABLE.value:
        failures.append(
            {
                "repair_outcome": repair.get("outcome"),
                "attribution": repair.get("attribution"),
                "failure_run_ids": repair.get("failure_run_ids", []),
                "summary": repair.get("summary"),
            }
        )
    return failures


def _reference(project: Project, stage: object, kind: object) -> dict[str, Any]:
    return {"stage": stage.value, **project.artifact(stage, kind).to_dict()}


def _json(value: Any) -> bytes:
    return (canonical_json(value) + "\n").encode()
