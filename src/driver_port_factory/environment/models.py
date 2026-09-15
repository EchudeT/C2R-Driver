from __future__ import annotations

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


class RouteKind(StrEnum):
    DIRECT_QEMU = "direct-qemu"
    OFFICIAL_TARGET_RUNNER = "official-target-runner"
    CONTAINERIZED_QEMU = "containerized-qemu"
    QTEST_OR_QMP_HARNESS = "qtest-or-qmp-harness"
    SOURCE_BASELINE_RUNNER = "source-baseline-runner"


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
    purpose: str
    artifact_mode: ArtifactMode
    route_kind: RouteKind
    device_identity: str
    topology: str
    command: tuple[str, ...]
    cwd: str
    environment: dict[str, str]
    timeout_seconds: int
    expected_markers: tuple[str, ...]
    accepted_exit_codes: tuple[int, ...]
    accept_timeout: bool
    runner_evidence_paths: tuple[str, ...]
    relevance_evidence: str
    driver_insertion_or_packaging_path: str | None = None

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["artifact_mode"] = self.artifact_mode.value
        value["route_kind"] = self.route_kind.value
        value["milestone"] = self.milestone.value
        return value

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> ExperimentPlan:
        cls._validate_envelope(value)
        command = cls._string_list(value["command"], "command")
        markers = cls._string_list(value["expected_markers"], "expected_markers")
        evidence_paths = cls._string_list(value["runner_evidence_paths"], "runner_evidence_paths")
        exit_codes = cls._exit_codes(value["accepted_exit_codes"])
        environment = cls._environment(value.get("environment", {}))
        timeout = cls._timeout(value["timeout_seconds"])
        milestone = cls._milestone(value["milestone"])
        accept_timeout = value["accept_timeout"]
        if not exit_codes and not accept_timeout:
            raise WorkflowError(
                "experiment plan must accept at least one exit code or a bounded timeout"
            )
        return cls(
            schema_version=int(value["schema_version"]),
            route_id=value["route_id"],
            milestone=milestone,
            purpose=value["purpose"],
            artifact_mode=ArtifactMode(value["artifact_mode"]),
            route_kind=RouteKind(value["route_kind"]),
            device_identity=value["device_identity"],
            topology=value["topology"],
            command=tuple(command),
            cwd=value["cwd"],
            environment=dict(environment),
            timeout_seconds=timeout,
            expected_markers=tuple(markers),
            accepted_exit_codes=tuple(exit_codes),
            accept_timeout=accept_timeout,
            runner_evidence_paths=tuple(evidence_paths),
            relevance_evidence=value["relevance_evidence"],
            driver_insertion_or_packaging_path=value.get("driver_insertion_or_packaging_path"),
        )

    @staticmethod
    def _validate_envelope(value: dict[str, Any]) -> None:
        required = {
            "schema_version",
            "route_id",
            "milestone",
            "purpose",
            "artifact_mode",
            "route_kind",
            "device_identity",
            "topology",
            "command",
            "cwd",
            "timeout_seconds",
            "expected_markers",
            "accepted_exit_codes",
            "accept_timeout",
            "runner_evidence_paths",
            "relevance_evidence",
        }
        missing = sorted(required - value.keys())
        if missing:
            raise WorkflowError(f"experiment plan missing fields: {', '.join(missing)}")
        if value["schema_version"] != 1:
            raise WorkflowError("experiment plan schema_version must be 1")
        for field_name in (
            "route_id",
            "purpose",
            "device_identity",
            "topology",
            "cwd",
            "relevance_evidence",
        ):
            if not isinstance(value[field_name], str) or not value[field_name].strip():
                raise WorkflowError(f"experiment plan {field_name} must be non-empty")
        if not isinstance(value["accept_timeout"], bool):
            raise WorkflowError("accept_timeout must be boolean")

    @staticmethod
    def _string_list(value: Any, field: str) -> list[str]:
        if (
            not isinstance(value, list)
            or not value
            or not all(isinstance(item, str) and item for item in value)
        ):
            raise WorkflowError(f"experiment plan {field} must be a non-empty string list")
        return value

    @staticmethod
    def _exit_codes(value: Any) -> list[int]:
        if not isinstance(value, list) or not all(isinstance(item, int) for item in value):
            raise WorkflowError("accepted_exit_codes must be an integer list")
        return value

    @staticmethod
    def _environment(value: Any) -> dict[str, str]:
        if not isinstance(value, dict) or not all(
            isinstance(key, str) and isinstance(item, str) for key, item in value.items()
        ):
            raise WorkflowError("experiment plan environment must map strings to strings")
        return value

    @staticmethod
    def _timeout(value: Any) -> int:
        if not isinstance(value, int) or value < 1 or value > 3600:
            raise WorkflowError("timeout_seconds must be between 1 and 3600")
        return value

    @staticmethod
    def _milestone(value: Any) -> ExperimentRouteMilestone:
        try:
            milestone = ExperimentRouteMilestone(value)
        except (TypeError, ValueError) as error:
            raise WorkflowError("environment recovery plan milestone is invalid") from error
        if milestone is not ExperimentRouteMilestone.READY:
            raise WorkflowError("environment recovery plan milestone must be EXPERIMENT_READY")
        return milestone
