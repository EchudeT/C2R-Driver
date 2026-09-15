from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol


@dataclass(frozen=True, slots=True)
class CommandPlan:
    argv: tuple[str, ...]
    cwd: Path
    environment: Mapping[str, str]
    timeout_seconds: int


@dataclass(frozen=True, slots=True)
class PlatformIdentity:
    name: str
    revision: str
    source_digest: str


@dataclass(frozen=True, slots=True)
class DriverIdentity:
    canonical_name: str
    bus: str
    device_scope: tuple[str, ...]
    source_paths: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RuntimeArtifact:
    path: Path
    digest: str
    artifact_mode: str
    identity_probe: CommandPlan


class SourcePlatformPlugin(Protocol):
    name: str

    def resolve_driver(self, name: str, source_tree: Path) -> Sequence[DriverIdentity]: ...

    def dependency_closure_plan(
        self, identity: DriverIdentity, source_tree: Path
    ) -> Sequence[CommandPlan]: ...

    def reference_build_plan(self, identity: DriverIdentity) -> Sequence[CommandPlan]: ...

    def reference_run_plan(self, scenario_id: str) -> Sequence[CommandPlan]: ...


class TargetPlatformPlugin(Protocol):
    name: str

    def profile_queries(self) -> Sequence[str]: ...

    def analogous_driver_candidates(self, device_class: str) -> Sequence[str]: ...

    def integration_plan(self, translated_root: Path) -> Sequence[CommandPlan]: ...

    def artifact_plan(self, translated_root: Path) -> Sequence[CommandPlan]: ...

    def locate_runtime_artifact(self) -> RuntimeArtifact: ...


class DeviceClassPlugin(Protocol):
    device_class: str
    protocol_version: str

    def public_scenarios(self) -> Sequence[Any]: ...

    def encode_stimulus(self, scenario: Any) -> bytes: ...

    def normalize_observation(self, raw: bytes) -> Mapping[str, Any]: ...

    def check_oracle(self, scenario: Any, observation: Mapping[str, Any]) -> bool: ...


class EmulatorBackend(Protocol):
    name: str

    def baseline_plan(self, device: DriverIdentity) -> Sequence[CommandPlan]: ...

    def target_run_plan(
        self, artifact: RuntimeArtifact, scenario_id: str
    ) -> Sequence[CommandPlan]: ...

    def qmp_endpoint(self) -> str | None: ...

    def collect_observation(self, run_directory: Path) -> bytes: ...
