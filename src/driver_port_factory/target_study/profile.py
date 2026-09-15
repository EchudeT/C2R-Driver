from __future__ import annotations

from pathlib import Path
from typing import Any

from ..acquisition.contracts import AcquisitionArtifact, AcquisitionStage
from ..acquisition.models import CheckoutRecord, RepositoryRole
from ..core.models import WorkflowError
from ..core.project import Project
from ..environment.contracts import EnvironmentArtifact, EnvironmentStage
from .contracts import BaselineStatus, TargetProfileStatus
from .evidence import TargetEvidenceVerifier, require_fields

PROFILE_SECTIONS = frozenset(
    {
        "repository_documentation_map",
        "lifecycle_execution_contexts",
        "hardware_access_concurrency",
        "error_recovery_observability",
        "coding_safety_requirements",
        "artifact_qemu_path",
    }
)

PROFILE_HEADINGS = (
    "# Target-Platform Profile",
    "## Identity",
    "## Repository and documentation map",
    "## Closest analogous implementation",
    "## Lifecycle and execution contexts",
    "## Target API evidence",
    "## Hardware access and concurrency",
    "## Error, recovery and observability conventions",
    "## Coding and safety requirements",
    "## Artifact and QEMU path",
    "## Target changes and unresolved gaps",
)


class TargetProfileValidator:
    """Validate the target profile against acquisition, environment, and KB evidence."""

    def __init__(self, project: Project, evidence: TargetEvidenceVerifier) -> None:
        self.project = project
        self.evidence = evidence

    def validate(self, profile: dict[str, Any]) -> dict[str, Any]:
        require_fields(
            profile,
            {
                "schema_version",
                "target_platform",
                "source_root",
                "revision",
                "artifact_mode",
                "profile_status",
                "unmodified_target_baseline",
                *PROFILE_SECTIONS,
            },
            "target profile",
        )
        try:
            status = TargetProfileStatus(profile["profile_status"])
        except (KeyError, TypeError, ValueError) as error:
            raise WorkflowError("target profile has an invalid profile_status") from error
        if profile["schema_version"] != 1 or status is not TargetProfileStatus.READY:
            raise WorkflowError("target profile must be schema_version=1 and READY")
        target = self._target_checkout()
        self._validate_identity(profile, target)
        mode = self.project.load_json_artifact(
            EnvironmentStage.RECOVERY, EnvironmentArtifact.MODE_RECORD
        )["artifact_mode"]
        if profile["artifact_mode"] != mode:
            raise WorkflowError("target profile artifact_mode differs from environment recovery")
        self._validate_baseline(profile["unmodified_target_baseline"], target)
        return {
            "target_platform": profile["target_platform"],
            "revision": profile["revision"],
            "artifact_mode": profile["artifact_mode"],
            "sections": self._validate_sections(profile),
        }

    def _target_checkout(self) -> CheckoutRecord:
        acquisition = self.project.load_json_artifact(
            AcquisitionStage.EVIDENCE_ACQUISITION,
            AcquisitionArtifact.ACQUISITION_MANIFEST,
        )
        records = tuple(CheckoutRecord.from_dict(record) for record in acquisition["checkouts"])
        matches = [record for record in records if record.role is RepositoryRole.TARGET]
        if len(matches) != 1:
            raise WorkflowError("expected one target checkout")
        return matches[0]

    def _validate_identity(self, profile: dict[str, Any], target: CheckoutRecord) -> None:
        expected_root = str((self.project.root / target.checkout_path).resolve())
        if profile["target_platform"] != self.project.config.target_platform:
            raise WorkflowError("target profile platform differs from the project target")
        if str(Path(profile["source_root"]).resolve()) != expected_root:
            raise WorkflowError("target profile source_root is not the frozen target baseline")
        if profile["revision"] != target.resolved_commit:
            raise WorkflowError("target profile revision differs from the frozen target revision")

    @staticmethod
    def _validate_baseline(baseline: Any, target: CheckoutRecord) -> None:
        baseline = require_fields(
            baseline,
            {"revision", "tree_id", "clean", "baseline_status", "baseline_evidence"},
            "unmodified target baseline",
        )
        if baseline["revision"] != target.resolved_commit or baseline["tree_id"] != target.tree_id:
            raise WorkflowError("unmodified target baseline identity does not match acquisition")
        if baseline["clean"] is not True:
            raise WorkflowError("unmodified target baseline must be recorded clean")
        try:
            BaselineStatus(baseline["baseline_status"])
        except (KeyError, TypeError, ValueError) as error:
            raise WorkflowError("invalid unmodified target baseline status") from error
        if not str(baseline["baseline_evidence"]).strip():
            raise WorkflowError("unmodified target baseline requires evidence or a blocker reason")

    def _validate_sections(self, profile: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
        results = {}
        for section in sorted(PROFILE_SECTIONS):
            value = profile[section]
            if not isinstance(value, dict) or not str(value.get("summary", "")).strip():
                raise WorkflowError(f"target profile section {section} requires a summary")
            references = value.get("evidence_refs")
            if not isinstance(references, list) or not references:
                raise WorkflowError(
                    f"target profile section {section} requires target evidence_refs"
                )
            results[section] = [self.evidence.verify(reference) for reference in references]
        return results
