"""Explicit minimal repair routing, never inferred from prose or exit status."""
from ..acquisition.contracts import AcquisitionStage
from ..acquisition.repository import load_repository_acquisition
from ..codex.contracts import CodexOutputError
from ..core.contracts import StageKey
from ..core.models import WorkflowError
from ..environment.contracts import EnvironmentStage
from ..knowledge.index import file_sha256
from ..orchestration.protocol import REPAIR_TARGETS
from ..target_study.contracts import TargetStudyStage
from .contracts import MigrationStage
from .public_qemu import PublicQemuService, current_public_qemu_run


def retry_prerequisite(project, target, *, trigger, reason):
    """Keep evidence in feedback; count unchanged executable premises, not new reports."""
    from ..core.models import ArtifactDirection
    refs = project.current_artifact_refs(stage=target, direction=ArtifactDirection.OUTPUT)
    selected = {
        "migration_contracts": {"migration_contracts", "test_port_matrix"},
        "target_platform_study": {"target_study_report"},
        "target_framework_enablement": {
            "target_framework_enablement_bundle",
            "target_framework_enablement_report",
            "target_framework_change_inventory",
        },
        "artifact_preparation": {"runtime_artifact"},
    }
    if target.value == "driver_implementation":
        from .contracts import MigrationArtifact
        bundle = project.load_json_artifact(target, MigrationArtifact.IMPLEMENTATION_BUNDLE)
        progress = {"files": bundle["files"], "upstream": bundle["target_worktree"]}
    elif target.value == "public_qemu_validation":
        from .contracts import MigrationArtifact
        report = project.load_json_artifact(target, MigrationArtifact.PUBLIC_QEMU_REPORT)
        run = current_public_qemu_run(report)
        acquisition = load_repository_acquisition(project)
        worktree = project.root / acquisition.target_worktree.path
        script_path = worktree / ".dpf-output" / "public-qemu.sh"
        progress = {"runtime": run["runtime_artifact"]["sha256"],
                    "script": run["script"]["sha256"], "helpers": run["helper_inputs"]}
        # A reviewer may return an unchanged receipt after the worker has
        # already repaired the current harness. Include live execution inputs
        # so the persisted retry guard recognizes that substantive progress.
        # The frozen receipt remains part of the identity for auditability.
        progress["current_script"] = (
            file_sha256(script_path) if script_path.is_file() else None
        )
        progress["current_helpers"] = PublicQemuService._helper_inputs(worktree)
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
    MigrationStage.TARGET_FRAMEWORK_ENABLEMENT,
    MigrationStage.DRIVER_IMPLEMENTATION,
    MigrationStage.ARTIFACT_PREPARATION,
    MigrationStage.PUBLIC_QEMU_VALIDATION,
)}

assert set(ROUTES) == REPAIR_TARGETS


class WorkerBlocked(WorkflowError):
    """An honest missing prerequisite, not a malformed answer to retry three times."""


class PrerequisiteRepair(WorkflowError):
    def __init__(self, target: StageKey, report: str) -> None:
        super().__init__(f"worker requested {target.value}; frozen report: {report}")
        self.target = target
