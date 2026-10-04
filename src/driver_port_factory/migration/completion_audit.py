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
        repair = (
            self._document(
                project,
                MigrationStage.FINAL_EVIDENCE_REVIEW,
                MigrationArtifact.FINAL_EVIDENCE_REVIEW_REPORT,
            )
            if project.config.enable_final_evidence_review
            else {"review_mode": "disabled"}
        )
        run = public.get("run")
        runs = [run] if isinstance(run, dict) else []
        runtime_ref = project.artifact(
            MigrationStage.ARTIFACT_PREPARATION, MigrationArtifact.RUNTIME_ARTIFACT
        )
        driver_presence = identity.get("driver_presence")
        lineage_verified = (
            identity.get("runtime_artifact", {}).get("sha256") == runtime_ref.digest
            and isinstance(driver_presence, dict)
            and bool(driver_presence)
        )
        runtime_observation = audit_runtime(public, runtime_ref.digest, lineage_verified)
        runtime_observed = runtime_observation["runtime_bound"]
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
                {"runtime_artifact_sha256": runtime_ref.digest} if runtime_observed else None
            ),
        }
        unresolved = []
        if not lineage_verified:
            unresolved.append({"kind": "artifact_lineage", "status": "FAIL"})
        if not runtime_observed:
            unresolved.append({"kind": "current_runtime_on_qemu", "status": "NOT_VERIFIED"})
        snapshot_digest = hashlib.sha256(_json(artifacts)).hexdigest()
        blind = self._blind_candidate(project)
        audit = {
            "schema_version": 1,
            "audit_status": AUDIT_STATUS,
            "worker_acceptances": [
                {"stage": item.name.value, "decision": item.message}
                for item in project.stages()
                if (item.message or "").startswith("WORKER_ACCEPTED:")
            ],
            "evaluation_mode": project.config.evaluation_mode.value,
            "stage_results": stages,
            "artifact_snapshot": artifacts,
            "artifact_snapshot_sha256": snapshot_digest,
            "work_products": {
                "contract_and_test_results": _reference(
                    project,
                    MigrationStage.PUBLIC_QEMU_VALIDATION,
                    MigrationArtifact.PUBLIC_QEMU_WORK_REPORT,
                ),
                "runtime_evidence_review": repair.get("review"),
                "review_mode": repair["review_mode"],
                "review_decision": repair.get("outcome"),
                "contracts": _reference(
                    project, MigrationStage.CONTRACTS, MigrationArtifact.CONTRACTS
                ),
                "tests": _reference(
                    project, MigrationStage.CONTRACTS, MigrationArtifact.TEST_PORT_MATRIX
                ),
                "translation_and_compliance": _reference(
                    project,
                    MigrationStage.DRIVER_IMPLEMENTATION,
                    MigrationArtifact.COMPLIANCE_REPORT,
                ),
                "target_changes": _reference(
                    project,
                    MigrationStage.DRIVER_IMPLEMENTATION,
                    MigrationArtifact.TARGET_CHANGE_INVENTORY,
                ),
            },
            "artifact_lineage": lineage,
            "public_runs": runs,
            "failure_attribution": _failure_attribution(public),
            "unresolved": unresolved,
            "blind_candidate": blind,
            "scope_limits": {
                "semantic_coverage": review_scope(repair),
                "mechanical_checks": (
                    "artifact hashes, observed execution, boot-argument binding and fresh logs"
                ),
                "oracle_author": "worker; public self-check is not independent evaluation",
                "qemu_evidence": runtime_observation["classification"],
                "functional_driver_execution": "SEE_INDEPENDENT_REVIEW_AND_CONTRACT_RESULTS",
                "integration_boundary": (
                    None if runtime_observed else "CURRENT_RUNTIME_EXECUTION_NOT_ESTABLISHED"
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


def audit_runtime(public, runtime_digest, lineage_verified):
    """Report collector evidence without promoting harness PASS to device correctness."""
    run = public.get("run", {})
    bound = (
        lineage_verified
        and run.get("execution_status") == "PASS"
        and run.get("runtime_artifact", {}).get("sha256") == runtime_digest
        and run.get("exec_trace", {}).get("runtime_bound") is True
        and bool(run.get("exec_trace", {}).get("qemu_execs"))
        and bool(run.get("logs"))
        and run.get("attribution") == PublicRunAttribution.PUBLIC_HARNESS.value
    )
    return {
        "runtime_bound": bound,
        "classification": (
            "CURRENT_RUNTIME_ON_QEMU_PUBLIC_HARNESS"
            if bound
            else "CURRENT_RUNTIME_EXECUTION_NOT_ESTABLISHED"
        ),
    }


def review_scope(review):
    if review.get("review_mode") == "independent" and review.get("outcome") == "PASS":
        return "INDEPENDENT_REVIEW_RECORDED_NOT_EXHAUSTIVE_OR_BLIND_VERIFICATION"
    return "WORKER_SELF_CHECK_NOT_INDEPENDENT_CONTRACT_VERIFICATION"


def validate_completion_audit_bundle(context: BundleValidationContext) -> None:
    audit = json_object(
        context.one_current(MigrationArtifact.EVIDENCE_AUDIT)[1],
        MigrationArtifact.EVIDENCE_AUDIT.value,
    )
    expected_stages, expected_artifacts = _database_snapshot(context.project_root)
    from ..composition import open_project

    project = open_project(context.project_root, read_only=True, verify_artifacts=False)
    review = (
        json_object(
            context.one_dependency(MigrationArtifact.FINAL_EVIDENCE_REVIEW_REPORT)[1],
            "final evidence review",
        )
        if project.config.enable_final_evidence_review
        else {"review_mode": "disabled"}
    )
    if audit.get("work_products", {}).get("review_decision") != review.get("outcome") or audit.get(
        "scope_limits", {}
    ).get("semantic_coverage") != review_scope(review):
        raise WorkflowError("completion audit misstates the independent review outcome or scope")

    public = json_object(
        context.one_dependency(MigrationArtifact.PUBLIC_QEMU_REPORT)[1], "public QEMU report"
    )
    identity = json_object(
        context.one_dependency(MigrationArtifact.ARTIFACT_IDENTITY)[1], "artifact identity"
    )
    runtime_ref, _ = context.one_dependency(MigrationArtifact.RUNTIME_ARTIFACT)
    presence = identity.get("driver_presence")
    lineage = (
        identity.get("runtime_artifact", {}).get("sha256") == runtime_ref.digest
        and isinstance(presence, dict)
        and bool(presence)
    )
    observation = audit_runtime(public, runtime_ref.digest, lineage)
    expected_binding = (
        {"runtime_artifact_sha256": runtime_ref.digest} if observation["runtime_bound"] else None
    )
    if (
        audit.get("scope_limits", {}).get("qemu_evidence") != observation["classification"]
        or audit.get("artifact_lineage", {}).get("verified") != lineage
        or audit.get("artifact_lineage", {}).get("public_qemu_binding") != expected_binding
        or audit.get("public_runs") != [public["run"]]
    ):
        raise WorkflowError("completion audit misstates current runtime execution evidence")

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


def _failure_attribution(public: dict[str, Any] | None) -> list[dict[str, Any]]:
    failures = [
        {
            "run_id": run.get("run_id"),
            "status": run.get("execution_status"),
            "attribution": run.get("attribution"),
        }
        for run in ([public["run"]] if isinstance((public or {}).get("run"), dict) else [])
        if run.get("execution_status") != ContractExecutionStatus.PASS.value
    ]
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
