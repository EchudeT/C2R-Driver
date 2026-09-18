from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from ..core.models import GeneratedArtifact, StageStatus, WorkflowError, utc_now
from ..core.project import Project
from ..core.validation import BundleValidationContext, json_object
from ..knowledge.index import file_sha256
from .contracts import ContractExecutionStatus, MigrationArtifact, MigrationStage


class PublicRepairService:
    def prepare(self, project: Project) -> tuple[dict[str, Any], dict[str, Any]] | None:
        ref = project.artifact(
            MigrationStage.PUBLIC_QEMU_VALIDATION,
            MigrationArtifact.PUBLIC_QEMU_REPORT,
        )
        report = json.loads(project.artifacts.read(ref))
        if report.get("execution_status") == ContractExecutionStatus.PASS.value:
            return None
        return ref.to_dict(), report

    def finalize_not_applicable(self, project: Project, *, review_path: Path) -> None:
        if project.stage(MigrationStage.PUBLIC_REPAIR).status is StageStatus.READY:
            project.start(MigrationStage.PUBLIC_REPAIR)
        self._finalize(
            project,
            {
                "schema_version": 2,
                "outcome": ContractExecutionStatus.NOT_APPLICABLE.value,
                "reason": "The public run passed independent evidence review.",
                "review": {
                    "path": str(review_path.relative_to(project.root)),
                    "sha256": file_sha256(review_path),
                    "text": review_path.read_text(encoding="utf-8"),
                },
                "recorded_at": utc_now(),
            },
        )

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
    outcome = ContractExecutionStatus(report.get("outcome"))
    if outcome is ContractExecutionStatus.NOT_APPLICABLE:
        review = report.get("review", {})
        text = review.get("text", "") if isinstance(review, dict) else ""
        if (
            not isinstance(text, str)
            or not text.rstrip().endswith("\nDPF_REVIEW: PASS")
            or hashlib.sha256(text.encode()).hexdigest() != review.get("sha256")
        ):
            raise WorkflowError("a passing public run requires an intact final evidence review")
    if outcome is ContractExecutionStatus.PASS:
        run = report.get("run")
        trace = run.get("exec_trace") if isinstance(run, dict) else None
        if (
            not isinstance(trace, dict)
            or not trace.get("qemu_execs")
            or trace.get("runtime_bound") is not True
            or not run.get("logs")
            or run.get("attribution") != "TARGET_DRIVER_ON_QEMU"
        ):
            raise WorkflowError("a passing repair lacks observed QEMU/runtime/log evidence")
