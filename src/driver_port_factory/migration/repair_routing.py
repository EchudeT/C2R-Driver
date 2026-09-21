"""Explicit minimal repair routing, never inferred from prose or exit status."""
from ..codex.contracts import CodexOutputError
from ..core.contracts import StageKey
from ..core.models import WorkflowError
from ..acquisition.contracts import AcquisitionStage
from ..environment.contracts import EnvironmentStage
from ..target_study.contracts import TargetStudyStage
from .contracts import MigrationStage


def retry_prerequisite(project, target, *, trigger, reason):
    """Keep evidence in feedback; count unchanged executable premises, not new reports."""
    from ..core.models import ArtifactDirection
    refs = project.current_artifact_refs(stage=target, direction=ArtifactDirection.OUTPUT)
    selected = {
        "migration_contracts": {"migration_contracts", "test_port_matrix"},
        "target_platform_study": {"target_study_report"},
        "artifact_preparation": {"runtime_artifact"},
    }
    if target.value == "driver_implementation":
        from .contracts import MigrationArtifact
        bundle = project.load_json_artifact(target, MigrationArtifact.IMPLEMENTATION_BUNDLE)
        progress = {"files": bundle["files"], "upstream": bundle["target_worktree"]}
    elif target.value == "public_qemu_validation":
        from .contracts import MigrationArtifact
        report = project.load_json_artifact(target, MigrationArtifact.PUBLIC_QEMU_REPORT)
        run = report["runs"][0]
        progress = {"runtime": run["runtime_artifact"]["sha256"],
                    "script": run["script"]["sha256"], "helpers": run["helper_inputs"]}
    elif target.value == "environment_recovery":
        from ..environment.contracts import EnvironmentArtifact
        attempt = project.load_json_artifact(target, EnvironmentArtifact.EXPERIMENT_READY_RUN)
        progress = attempt["repair_inputs"]
    elif target.value == "evidence_closure":
        from ..acquisition.contracts import AcquisitionArtifact
        plan = project.load_json_artifact(target, AcquisitionArtifact.EVIDENCE_CLOSURE_PLAN)
        progress = {key: plan[key] for key in (
            "migration_envelope_sha256", "repository_manifest_sha256", "facets")}
    else:
        kinds = selected.get(target.value)
        if kinds is None:
            raise CodexOutputError(f"No prerequisite progress identity for {target.value}")
        progress = sorted((ref.kind, ref.digest) for ref in refs if ref.kind in kinds)
        if {kind for kind, _ in progress} != kinds:
            raise CodexOutputError(f"Missing prerequisite progress inputs for {target.value}")
    project.retry_from(target, trigger=trigger, reason=reason, progress=progress)

ROUTES = {stage.value: stage for stage in (
    AcquisitionStage.EVIDENCE_CLOSURE,
    EnvironmentStage.RECOVERY,
    TargetStudyStage.STUDY,
    MigrationStage.CONTRACTS,
    MigrationStage.DRIVER_IMPLEMENTATION,
    MigrationStage.ARTIFACT_PREPARATION,
    MigrationStage.PUBLIC_QEMU_VALIDATION,
)}
from ..orchestration.protocol import REPAIR_TARGETS
assert set(ROUTES) == REPAIR_TARGETS


class WorkerBlocked(WorkflowError):
    """An honest missing prerequisite, not a malformed answer to retry three times."""


class PrerequisiteRepair(WorkflowError):
    def __init__(self, target: StageKey, report: str) -> None:
        super().__init__(f"worker requested {target.value}; frozen report: {report}")
        self.target = target


def repair_target(text: str) -> StageKey:
    markers = [line.strip().removeprefix("DPF_REPAIR_STAGE:").strip()
               for line in text.splitlines() if line.strip().startswith("DPF_REPAIR_STAGE:")]
    if len(markers) != 1 or markers[0] not in ROUTES:
        raise CodexOutputError(
            "REWORK requires exactly one DPF_REPAIR_STAGE: followed by "
            + ", ".join(ROUTES) + ". "
            "Choose the earliest actually affected stage: reviewed-source defect requires "
            "implementation; image/guest entrypoint requires packaging; "
            "harness/oracle/evidence with unchanged artifact requires public validation. "
            "Explain the cause in Markdown; do not reopen unaffected review conclusions."
        )
    return ROUTES[markers[0]]
