from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any

from ..core.models import WorkflowError


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


@dataclass(frozen=True, slots=True)
class ExperimentPlan:
    schema_version: int
    route_id: str
    milestone: str
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
        return value

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> ExperimentPlan:
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
        command = value["command"]
        markers = value["expected_markers"]
        exit_codes = value["accepted_exit_codes"]
        evidence_paths = value["runner_evidence_paths"]
        if (
            not isinstance(command, list)
            or not command
            or not all(isinstance(item, str) and item for item in command)
        ):
            raise WorkflowError("experiment plan command must be a non-empty string list")
        if (
            not isinstance(markers, list)
            or not markers
            or not all(isinstance(item, str) and item for item in markers)
        ):
            raise WorkflowError("EXPERIMENT_READY requires at least one expected marker")
        if not isinstance(exit_codes, list) or not all(
            isinstance(item, int) for item in exit_codes
        ):
            raise WorkflowError("accepted_exit_codes must be an integer list")
        if (
            not isinstance(evidence_paths, list)
            or not evidence_paths
            or not all(isinstance(item, str) and item for item in evidence_paths)
        ):
            raise WorkflowError("runner_evidence_paths must be a non-empty string list")
        environment = value.get("environment", {})
        if not isinstance(environment, dict) or not all(
            isinstance(key, str) and isinstance(item, str) for key, item in environment.items()
        ):
            raise WorkflowError("experiment plan environment must map strings to strings")
        timeout = value["timeout_seconds"]
        if not isinstance(timeout, int) or timeout < 1 or timeout > 3600:
            raise WorkflowError("timeout_seconds must be between 1 and 3600")
        if not isinstance(value["accept_timeout"], bool):
            raise WorkflowError("accept_timeout must be boolean")
        if not exit_codes and not value["accept_timeout"]:
            raise WorkflowError(
                "experiment plan must accept at least one exit code or a bounded timeout"
            )
        if value["milestone"] != "EXPERIMENT_READY":
            raise WorkflowError("environment recovery plan milestone must be EXPERIMENT_READY")
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
        return cls(
            schema_version=int(value["schema_version"]),
            route_id=value["route_id"],
            milestone=value["milestone"],
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
            accept_timeout=bool(value["accept_timeout"]),
            runner_evidence_paths=tuple(evidence_paths),
            relevance_evidence=value["relevance_evidence"],
            driver_insertion_or_packaging_path=value.get("driver_insertion_or_packaging_path"),
        )
