from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any

from ..core.models import WorkflowError
from .contracts import ExperimentRouteMilestone


class ArtifactMode(StrEnum):
    VERIFIED_LOCAL_RUNNER = "verified-local-runner"
    OFFICIAL_RUNNER = "official-runner"
    OFFICIAL_CONTAINER_OR_SDK = "official-container-or-sdk"
    PREBUILT_COMPONENT_INSERTION = "prebuilt-component-insertion"
    IMAGE_REPACK = "image-repack"
    CI_DERIVED_BUILD = "ci-derived-build"
    SOURCE_BUILD = "source-build"
    DIRECT_DEVICE_MODEL = "direct-device-model"
    SOURCE_BASELINE = "source-baseline"


class ExperimentReadiness(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"


class WorkspaceEntryKind(StrEnum):
    FILE = "file"
    DIRECTORY = "directory"


@dataclass(frozen=True, slots=True)
class ExperimentPlan:
    schema_version: int
    route_id: str
    milestone: ExperimentRouteMilestone
    purpose: Any
    artifact_mode: ArtifactMode
    device_identity: Any
    topology: Any
    command: tuple[str, ...]
    cwd: str
    environment: dict[str, str]
    timeout_seconds: int
    accepted_exit_codes: tuple[int, ...]
    runner_evidence_paths: tuple[str, ...]
    relevance_evidence: Any
    driver_insertion_or_packaging_path: Any | None = None
    executable_lock: dict[str, Any] | None = None
    frozen_repositories: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["artifact_mode"] = self.artifact_mode.value
        value["milestone"] = self.milestone.value
        for field in ("executable_lock", "frozen_repositories"):
            if value[field] is None:
                del value[field]
        return value

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> ExperimentPlan:
        cls._validate_envelope(value)
        lock = value.get("executable_lock")
        repositories = value.get("frozen_repositories")
        if any(item is not None and not isinstance(item, dict) for item in (lock, repositories)):
            raise WorkflowError("experiment frozen evidence must be an object")
        try:
            return cls(
                schema_version=3,
                route_id=(
                    value.get("route_id")
                    or "route-"
                    + hashlib.sha256(
                        json.dumps(
                            {
                                "artifact_mode": value["artifact_mode"],
                                "command": value["command"],
                                "cwd": value["cwd"],
                            },
                            sort_keys=True,
                        ).encode()
                    ).hexdigest()[:16]
                ),
                milestone=ExperimentRouteMilestone.READY,
                purpose=value.get("purpose"),
                artifact_mode=ArtifactMode(value["artifact_mode"]),
                device_identity=value.get("device_identity"),
                topology=value.get("topology"),
                command=tuple(cls._string_list(value["command"], "command")),
                cwd=value["cwd"],
                environment=cls._environment(value["environment"]),
                timeout_seconds=cls._timeout(value["timeout_seconds"]),
                accepted_exit_codes=tuple(cls._exit_codes(value["accepted_exit_codes"])),
                runner_evidence_paths=tuple(
                    cls._string_list(
                        value.get("runner_evidence_paths", []),
                        "runner_evidence_paths",
                        allow_empty=True,
                    )
                ),
                relevance_evidence=value.get("relevance_evidence"),
                driver_insertion_or_packaging_path=value.get("driver_insertion_or_packaging_path"),
                executable_lock=dict(lock) if lock is not None else None,
                frozen_repositories=(dict(repositories) if repositories is not None else None),
            )
        except (TypeError, ValueError) as error:
            raise WorkflowError("experiment plan has an invalid enum value") from error

    @staticmethod
    def _validate_envelope(value: dict[str, Any]) -> None:
        required = {
            "artifact_mode",
            "command",
            "cwd",
            "environment",
            "timeout_seconds",
            "accepted_exit_codes",
        }
        missing = sorted(required - value.keys())
        if missing:
            raise WorkflowError(f"experiment plan missing fields: {', '.join(missing)}")
        for name in ("cwd",):
            if not isinstance(value[name], str) or not value[name].strip():
                raise WorkflowError(f"experiment plan {name} must be non-empty")

    @staticmethod
    def _string_list(value: Any, field: str, *, allow_empty: bool = False) -> list[str]:
        if (
            not isinstance(value, list)
            or (not allow_empty and not value)
            or not all(isinstance(item, str) and item for item in value)
        ):
            raise WorkflowError(f"experiment plan {field} must be a non-empty string list")
        return value

    @staticmethod
    def _exit_codes(value: Any) -> list[int]:
        if (
            not isinstance(value, list)
            or not value
            or not all(isinstance(item, int) and not isinstance(item, bool) for item in value)
        ):
            raise WorkflowError("accepted_exit_codes must be a non-empty integer list")
        return value

    @staticmethod
    def _environment(value: Any) -> dict[str, str]:
        if not isinstance(value, dict) or not all(
            isinstance(key, str) and isinstance(item, str) for key, item in value.items()
        ):
            raise WorkflowError("experiment plan environment must map strings to strings")
        return dict(value)

    @staticmethod
    def _timeout(value: Any) -> int:
        if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= 3600:
            raise WorkflowError("timeout_seconds must be between 1 and 3600")
        return value
