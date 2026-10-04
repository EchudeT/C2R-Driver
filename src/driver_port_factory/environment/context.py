"""Actionable environment inputs, leaving full inventories and command logs on disk."""

import sys

from ..acquisition.navigation import repositories
from ..core.models import ArtifactDirection
from .contracts import EnvironmentArtifact as A
from .contracts import EnvironmentStage as S


def context(project):
    from ..platform.worker import context as platform_context

    platform = platform_context(project)
    if platform:
        return {
            **repositories(project),
            "platform_execution": platform,
            "bootstrap": {
                "tool": "driver_checks.platform",
                "arguments": {"action": "bootstrap"},
            },
            "instruction": "The operator has already selected the Docker route shown in "
            "platform_execution.configured_route. Call bootstrap once and submit its report. "
            "No host-based route inference, image discovery or worker-authored probe is needed. "
            "On failure retain the receipt and report the exact error; no automatic fallback.",
        }

    inventory = project.load_json_artifact(
        S.RECOVERY, A.INVENTORY, direction=ArtifactDirection.INPUT
    )
    modes = project.load_json_artifact(
        S.RECOVERY, A.MODE_CANDIDATES, direction=ArtifactDirection.INPUT
    )
    metadata = inventory.get("existing_operational_metadata", [])
    return {
        **repositories(project),
        "platform_execution": platform,
        "host": inventory["host"],
        "available_tools": {t["name"]: t["path"] for t in inventory["tools"] if t["available"]},
        "local_probes": inventory.get("local_probes", []),
        "operational_metadata": metadata[:20],
        "operational_metadata_total": len(metadata),
        "modes": [
            {
                "artifact_mode": m["artifact_mode"],
                "reason": m["reason"],
                "evidence_paths": m["evidence_paths"][:5],
                "status": m["status"],
            }
            for m in modes["candidates"]
        ],
        "bootstrap": {
            "tool": "driver_checks.platform",
            "arguments": {"action": "bootstrap"},
        },
        "prepare_smoke": [
            sys.executable,
            "-m",
            "driver_port_factory.cli",
            "environment",
            "prepare-smoke",
            str(project.root),
            "--image",
            "<LOCAL_IMAGE>",
            "--probe",
            "<PROBE.sh>",
        ],
        "instruction": "Use these observed facts before issuing discovery commands. Metadata paths "
        "are a bounded navigation sample, not exhaustive evidence. Read the versioned image/route "
        "definition only for an unsupported adapter; managed execution already has configured_route. "
        "Do not infer acceleration from host OS or page through inventories to choose an image. "
        "prepare_smoke generates the Docker envelope without running a probe or accepting a stage. "
        "It uses controller-owned container creation, descendant exec tracing, bounded execution "
        "and cleanup; no process polling or sleeps. "
        "Infrastructure errors stop without paid repair. "
        "For unsupported adapters only, prepare a route check with prepare_smoke. "
        "Managed bootstrap requires no worker-authored probe. Reusable recipes require matching "
        "target/QEMU revisions, architecture and image identity, and fresh current execution. "
        "Environment device-model readiness does not prove target build/boot or driver readiness.",
    }
