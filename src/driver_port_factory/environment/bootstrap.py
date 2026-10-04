"""Compose the fixed platform steps without model bookkeeping or automatic recovery."""

import json
import signal
from pathlib import Path

from ..core.models import WorkflowError
from ..migration.implementation import worktree_files
from ..platform import service
from .contracts import EnvironmentArtifact as A
from .contracts import EnvironmentStage as S
from .feedback import validation_summary


def _baseline(project, image, accelerator):
    profiles = [
        r for r in project.current_artifact_refs(stage=S.RECOVERY) if r.kind == A.PLATFORM_PROFILE
    ]
    if not profiles:
        service.prepare(project)
        return service.verify(project)
    profile, worktree = service.load(project)
    if (profile["image"], profile["accelerator"]) != (image, accelerator):
        raise WorkflowError("Bootstrap cannot replace the selected route; no automatic fallback")
    service.check_image(project, profile)
    if worktree_files(worktree, profile["target_revision"]):
        raise WorkflowError("Bootstrap requires the clean target baseline")
    # Never interpret a stale/missing/failed validation as permission to rebuild.
    # Explicit platform verify remains available for an authorized later attempt.
    value = service.verified(project)
    ref = max(
        (
            r
            for r in project.current_artifact_refs(stage=S.RECOVERY)
            if r.kind == A.PLATFORM_VALIDATION
        ),
        key=lambda r: r.ordinal,
    )
    return validation_summary(value, project.artifacts.path_for_digest(ref.digest))


def prepare(project):
    from ..platform.configuration import selected

    image, accelerator = selected(project.config)
    service.active(project, environment=True)
    if not service.required(project):
        raise WorkflowError("Bootstrap requires the configured managed Asterinas platform")
    baseline = _baseline(project, image, accelerator)
    work = project.root / "work/stage-work/environment_recovery"
    work.mkdir(parents=True, exist_ok=True)
    report = work / "environment-readiness.md"
    report.write_text(
        "# Environment readiness\n\n"
        f"Selected route: {image}; accelerator: {accelerator}.\n\n"
        "The controller observed a clean baseline build and interactive guest boot.\n"
        f"Baseline evidence: {baseline['receipt']}\n\n"
        "Device semantics and migrated-driver runtime: NOT_RUN. Resolve design-changing "
        "device questions on demand during joint analysis; implement and test driver behavior "
        "during delivery. This report is not a stage verdict.\n"
    )
    return {
        "status": "BASELINE_VERIFIED",
        "baseline": baseline,
        "report": str(report),
        "next": "Submit this report. The controller binds the existing baseline receipt; "
        "no additional device probe, environment script or rebuild is required.",
    }


def artifacts(project):
    """Derive environment outputs from the executed platform receipt, with no second run."""
    from ..core.models import ArtifactDirection
    from .documents import json_artifact
    from .models import ArtifactMode

    binding = service.acceptance_binding(project)
    receipt = service.verified(project)
    profile, _ = service.load(project)
    route_id = "managed-platform-baseline"
    mode = {
        "artifact_mode": ArtifactMode.OFFICIAL_CONTAINER_OR_SDK.value,
        "selected_route_id": route_id,
    }
    route = {
        "schema_version": 3,
        "route_id": route_id,
        **mode,
        "milestone": "EXPERIMENT_READY",
        "platform_execution": binding,
        "migrated_driver_runtime_ready": False,
        "mechanical_readiness": "PASS",
        "scope": "clean baseline build and interactive guest boot; no device verdict",
    }
    observed = {
        "schema_version": 3,
        "readiness": "PASS",
        "route": route,
        "command": {"argv": profile["build_argv"], "image_id": profile["image_id"]},
        "platform_validation": receipt,
    }
    return (
        *(
            json_artifact(
                kind,
                project.load_json_artifact(S.RECOVERY, kind, direction=ArtifactDirection.INPUT),
            )
            for kind in (A.INVENTORY, A.MODE_CANDIDATES)
        ),
        json_artifact(A.MODE_RECORD, mode),
        json_artifact(A.EXPERIMENT_READY_RUN, observed),
        json_artifact(A.EXPERIMENT_ROUTE, route),
    )


def accept(project):
    service.active(project, environment=True)
    project.finalize_stage(S.RECOVERY, artifacts(project))


def command(args):
    from ..composition import open_project
    from ..platform.cli import _cancel

    previous = signal.signal(signal.SIGTERM, _cancel)
    try:
        project = open_project(Path(args.path))
        print(json.dumps(prepare(project)))
    finally:
        signal.signal(signal.SIGTERM, previous)


def register(commands):
    parser = commands.add_parser(
        "bootstrap", help="verify baseline and prepare handoff; no acceptance"
    )
    parser.add_argument("path")
    parser.set_defaults(handler=command)
