"""Execution-container policy for target runtime experiments."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def target_requires_asterinas_container(target_platform: str) -> bool:
    """Asterinas experiments execute QEMU/build steps in its dev image."""

    return target_platform.strip().casefold() == "asterinas"


def is_asterinas_dev_image(image: str) -> bool:
    """Accept official ``asterinas/dev`` tags or digest-pinned references."""

    reference = image.strip().split("@", 1)[0]
    parts = reference.split("/")
    if parts and ":" in parts[-1]:
        parts[-1] = parts[-1].split(":", 1)[0]
    repository = parts[-2:]
    if len(repository) != 2 or repository != ["asterinas", "dev"]:
        return False
    return True


def load_container_observations(path: Path) -> dict[str, Any]:
    """Load the controller-owned Docker observation file without trusting prose."""

    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {"observations": [], "errors": ["container observation file unavailable"]}
    if not isinstance(value, dict):
        return {"observations": [], "errors": ["container observation file is not an object"]}
    observations = value.get("observations")
    errors = value.get("errors")
    return {
        "observations": observations if isinstance(observations, list) else [],
        "errors": errors if isinstance(errors, list) else [],
    }


def qemu_container_observations(path: Path) -> tuple[dict[str, Any], ...]:
    """Return fresh container observations whose top process is a QEMU binary."""

    value = load_container_observations(path)
    result = []
    for item in value["observations"]:
        if not isinstance(item, dict):
            continue
        argv = item.get("argv")
        image = item.get("image")
        if (
            isinstance(argv, list)
            and argv
            and isinstance(argv[0], str)
            and Path(argv[0]).name.startswith("qemu-system-")
            and isinstance(image, str)
        ):
            result.append(item)
    return tuple(result)


def container_execution_summary(
    *,
    observations_path: Path,
    target_platform: str,
    host_qemu_execs: tuple[str, ...],
) -> dict[str, Any]:
    """Summarize the mechanical target-container boundary for a run."""

    observation_data = load_container_observations(observations_path)
    records = qemu_container_observations(observations_path)
    images = sorted({record["image"] for record in records})
    required = target_requires_asterinas_container(target_platform)
    official = bool(records) and all(is_asterinas_dev_image(image) for image in images)
    # A run that invokes QEMU on the host as well as in the target container is
    # ambiguous: the controller must not attribute host execution to Asterinas.
    satisfied = not required or (official and not host_qemu_execs)
    return {
        "required": required,
        "satisfied": satisfied,
        "images": images,
        "image_ids": sorted({
            str(record["image_id"])
            for record in records
            if isinstance(record.get("image_id"), str)
        }),
        "qemu_programs": [record["argv"][0] for record in records],
        "observation_errors": [
            str(error) for error in observation_data["errors"]
            if isinstance(error, str)
        ],
        "observation_path": str(observations_path),
    }
