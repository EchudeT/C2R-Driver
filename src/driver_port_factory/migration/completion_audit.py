from __future__ import annotations

import hashlib
import sqlite3
from collections import Counter
from pathlib import Path
from typing import Any

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
from .contracts import (
    ContractEvidenceStatus,
    ContractExecutionStatus,
    MigrationArtifact,
    MigrationStage,
    PublicRunAttribution,
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
        identity = self._document(
            project, MigrationStage.ARTIFACT_PREPARATION, MigrationArtifact.ARTIFACT_IDENTITY
        )
        public = self._document(
            project, MigrationStage.PUBLIC_QEMU_VALIDATION, MigrationArtifact.PUBLIC_QEMU_REPORT
        )
        repair = self._document(
            project, MigrationStage.PUBLIC_REPAIR, MigrationArtifact.PUBLIC_REPAIR_REPORT
        )
        runs = list(public.get("runs", []))
        if repair.get("outcome") == ContractExecutionStatus.PASS.value:
            repair_run = dict(repair.get("run", {}))
            repair_run.setdefault("run_id", "public-repair-confirmation")
            repair_run.setdefault("contract_ids", [])
            repair_run.setdefault("test_ids", [])
            repair_run.setdefault("evidence_status", ContractEvidenceStatus.VERIFIED.value)
            runs.append(repair_run)
        target_driver_ran = any(
            run.get("execution_status") == ContractExecutionStatus.PASS.value
            and run.get("attribution") == PublicRunAttribution.TARGET_DRIVER_ON_QEMU.value
            for run in runs
        )
        runtime_ref = project.artifact(
            MigrationStage.ARTIFACT_PREPARATION, MigrationArtifact.RUNTIME_ARTIFACT
        )
        driver_presence = identity.get("driver_presence")
        if repair.get("outcome") == ContractExecutionStatus.PASS.value:
            driver_presence = {
                **(driver_presence if isinstance(driver_presence, dict) else {}),
                "repaired_runtime_artifact": repair.get("runtime_artifact"),
                "repair_work_report": repair.get("work_report"),
            }
        lineage_verified = (
            identity.get("runtime_artifact", {}).get("sha256") == runtime_ref.digest
            and isinstance(driver_presence, dict)
            and bool(driver_presence)
        )
        lineage = {
            "verified": lineage_verified,
            "implementation_sha256": project.artifact(
                MigrationStage.DRIVER_IMPLEMENTATION,
                MigrationArtifact.IMPLEMENTATION_BUNDLE,
            ).digest,
            "artifact_identity_sha256": project.artifact(
                MigrationStage.ARTIFACT_PREPARATION,
                MigrationArtifact.ARTIFACT_IDENTITY,
            ).digest,
            "runtime_artifact_sha256": runtime_ref.digest,
            "driver_presence": driver_presence,
            "artifact_mode": identity.get("artifact_mode"),
            "public_qemu_binding": (
                {"runtime_artifact_sha256": runtime_ref.digest}
                if target_driver_ran
                else None
            ),
        }
        unresolved = []
        if not lineage_verified:
            unresolved.append({"kind": "artifact_lineage", "status": "FAIL"})
        if not target_driver_ran:
            unresolved.append({"kind": "target_driver_on_qemu", "status": "NOT_VERIFIED"})
        snapshot_digest = hashlib.sha256(_json(artifacts)).hexdigest()
        blind = self._blind_candidate(project)
        audit = {
            "schema_version": 1,
            "audit_status": AUDIT_STATUS,
            "evaluation_mode": project.config.evaluation_mode.value,
            "stage_results": stages,
            "artifact_snapshot": artifacts,
            "artifact_snapshot_sha256": snapshot_digest,
            "contract_results": [],
            "test_results": [],
            "work_products": {
                "contracts": _reference(
                    project, MigrationStage.CONTRACTS, MigrationArtifact.CONTRACTS
                ),
                "tests": _reference(
                    project, MigrationStage.TEST_ADAPTATION, MigrationArtifact.TEST_PORT_MATRIX
                ),
                "translation_and_compliance": _reference(
                    project,
                    MigrationStage.DRIVER_IMPLEMENTATION,
                    MigrationArtifact.TRANSLATION_COVERAGE,
                ),
                "target_changes": _reference(
                    project,
                    MigrationStage.DRIVER_IMPLEMENTATION,
                    MigrationArtifact.TARGET_CHANGE_INVENTORY,
                ),
                "compliance": _reference(
                    project, MigrationStage.TARGET_COMPLIANCE, MigrationArtifact.COMPLIANCE_REPORT
                ),
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
                "integration_boundary": (
                    None if target_driver_ran else "BLOCKED_FULL_INTEGRATION"
                ),
                "real_hardware": KnowledgeEvidenceStatus.NOT_RUN.value,
                "private_evaluation": (
                    PrivateEvaluationState.NOT_RUN_BY_MIGRATOR.value
                    if blind is not None
                    else ContractExecutionStatus.NOT_APPLICABLE.value
                ),
            },
            "summary": {
                "execution_status_counts": dict(
                    sorted(Counter(run.get("execution_status") for run in runs).items())
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
        or "verified" not in audit.get("artifact_lineage", {})
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


def _execution_status(default: str, observations: list[dict[str, Any]]) -> str:
    """Conservatively combine captured execution observations."""

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


def _reference(project: Project, stage: object, kind: object) -> dict[str, Any]:
    return {"stage": stage.value, **project.artifact(stage, kind).to_dict()}


def _json(value: Any) -> bytes:
    return (canonical_json(value) + "\n").encode()
