from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..core.models import GeneratedArtifact, StageStatus, WorkflowError, utc_now
from ..core.project import Project
from ..core.validation import BundleValidationContext, json_object
from .contracts import (
    ContractExecutionStatus,
    MigrationArtifact,
    MigrationStage,
    RepairAction,
    RepairAttribution,
)
from .implementation import DriverImplementationService, ImplementationResponse


@dataclass(frozen=True, slots=True)
class PublicRepairDecision:
    attribution: RepairAttribution
    action: RepairAction
    failure_run_ids: tuple[str, ...]
    summary: str
    evidence: tuple[dict[str, Any], ...]
    implementation: ImplementationResponse | None

    @classmethod
    def read(cls, path: Path) -> PublicRepairDecision:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise WorkflowError("Codex public repair response is not UTF-8 JSON") from error
        if not isinstance(value, dict) or value.get("schema_version") != 2:
            raise WorkflowError("public repair response must be schema_version=2")
        evidence = value.get("evidence")
        run_ids = value.get("failure_run_ids")
        if (
            not isinstance(evidence, list)
            or not all(isinstance(item, dict) for item in evidence)
            or not isinstance(run_ids, list)
            or not all(isinstance(item, str) and item for item in run_ids)
        ):
            raise WorkflowError("public repair evidence or run IDs are invalid")
        try:
            action = RepairAction(value["action"])
            implementation = (
                ImplementationResponse.from_dict(value["implementation"])
                if value.get("implementation") is not None
                else None
            )
            decision = cls(
                RepairAttribution(value["attribution"]),
                action,
                tuple(run_ids),
                str(value["summary"]),
                tuple(evidence),
                implementation,
            )
        except (KeyError, TypeError, ValueError) as error:
            raise WorkflowError("public repair response has an invalid boundary") from error
        if (
            not decision.failure_run_ids
            or not decision.summary.strip()
            or not decision.evidence
            or (decision.action is RepairAction.APPLY) != (decision.implementation is not None)
            or (decision.action is RepairAction.APPLY and not decision.attribution.writable)
        ):
            raise WorkflowError("public repair response is incomplete")
        return decision


class PublicRepairService:
    def prepare(self, project: Project) -> tuple[dict[str, Any], dict[str, Any]] | None:
        public = project.stage(MigrationStage.PUBLIC_QEMU_VALIDATION)
        if public.status is StageStatus.PASS:
            return None
        reference, failure = self._latest_attempt(project)
        if public.status is StageStatus.RUNNING:
            project.complete(MigrationStage.PUBLIC_QEMU_VALIDATION, StageStatus.FAIL)
        elif public.status is not StageStatus.FAIL:
            raise WorkflowError("public QEMU validation has no repairable result")
        return reference.to_dict(), failure

    def finalize_not_applicable(self, project: Project) -> None:
        if project.stage(MigrationStage.PUBLIC_REPAIR).status is StageStatus.READY:
            project.start(MigrationStage.PUBLIC_REPAIR)
        self._finalize(
            project,
            {
                "schema_version": 2,
                "outcome": ContractExecutionStatus.NOT_APPLICABLE.value,
                "reason": "The public QEMU evidence ladder has no failed run.",
                "recorded_at": utc_now(),
            },
        )

    def apply(
        self,
        project: Project,
        decision: PublicRepairDecision,
        failure: dict[str, Any],
    ) -> None:
        failed_ids = {
            str(run["run_id"])
            for run in failure.get("runs", [])
            if run.get("execution_status") == ContractExecutionStatus.FAIL.value
        }
        if not set(decision.failure_run_ids) <= failed_ids:
            raise WorkflowError("public repair is not bound to failed runs")
        if decision.action is RepairAction.BLOCKED:
            self._finalize(
                project,
                {
                    "schema_version": 2,
                    "outcome": ContractExecutionStatus.BLOCKED.value,
                    "attribution": decision.attribution.value,
                    "failure_run_ids": list(decision.failure_run_ids),
                    "summary": decision.summary,
                    "evidence": list(decision.evidence),
                    "recorded_at": utc_now(),
                },
            )
            return

        project.retry_from(
            MigrationStage.DRIVER_IMPLEMENTATION,
            trigger=MigrationStage.PUBLIC_REPAIR,
            reason=f"public QEMU repair: {decision.summary}",
        )
        project.start(MigrationStage.DRIVER_IMPLEMENTATION)
        DriverImplementationService().finalize(project, decision.implementation)

    @staticmethod
    def _latest_attempt(project: Project):
        refs = [
            ref
            for ref in project.artifact_refs(stage=MigrationStage.PUBLIC_QEMU_VALIDATION)
            if ref.kind == MigrationArtifact.PUBLIC_QEMU_ATTEMPT.value and ref.ordinal is not None
        ]
        if not refs:
            raise WorkflowError("public QEMU validation has no preserved attempt")
        ref = max(refs, key=lambda item: item.ordinal)
        value = json.loads(project.artifacts.read(ref))
        if not isinstance(value, dict) or value.get("status") != StageStatus.FAIL.value:
            raise WorkflowError("latest public QEMU attempt is not a failure")
        return ref, value

    @staticmethod
    def _finalize(project: Project, report: dict[str, Any]) -> None:
        project.finalize_stage(
            MigrationStage.PUBLIC_REPAIR,
            (
                GeneratedArtifact(
                    MigrationArtifact.PUBLIC_REPAIR_REPORT,
                    (
                        json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
                    ).encode(),
                    "generated:public-repair",
                ),
            ),
        )


def validate_public_repair_bundle(context: BundleValidationContext) -> None:
    report = json_object(
        context.one_current(MigrationArtifact.PUBLIC_REPAIR_REPORT)[1],
        MigrationArtifact.PUBLIC_REPAIR_REPORT.value,
    )
    if report.get("schema_version") != 2:
        raise WorkflowError("public repair report has an invalid version")
    ContractExecutionStatus(report.get("outcome"))
