from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType

from ..core.models import WorkflowError


class EvidenceLane(StrEnum):
    SOURCE = "source"
    TARGET = "target"
    QEMU = "qemu"
    HARDWARE = "hardware"
    TEST = "test"
    TOOLING = "tooling"


class SourceFacet(StrEnum):
    DRIVER_ENTRY = "driver_entry"
    DEPENDENCY_CLOSURE = "dependency_closure"
    FRAMEWORK_CONTRACTS = "framework_contracts"
    BUILD_CONFIGURATION = "build_configuration"


class TargetFacet(StrEnum):
    EXTENSION_POINTS = "extension_points"
    DRIVER_FRAMEWORK = "driver_framework"
    ANALOGOUS_IMPLEMENTATIONS = "analogous_implementations"
    API_DEFINITIONS_AND_CALLS = "api_definitions_and_calls"
    LIFECYCLE = "lifecycle"
    ARTIFACT_RUNTIME_PATH = "artifact_runtime_path"
    CODING_AND_SAFETY_RULES = "coding_and_safety_rules"
    TEST_CONVENTIONS = "test_conventions"
    RELEASE_ASSETS = "release_assets"


class QemuFacet(StrEnum):
    DEVICE_MODEL = "device_model"
    DEVICE_MODEL_DOCUMENTATION = "device_model_documentation"
    OBSERVABILITY_AND_INJECTION = "observability_and_injection"


class HardwareFacet(StrEnum):
    DEVICE_MANUAL = "device_manual"
    REGISTER_STATE_MACHINE = "register_state_machine"
    BUS_SPECIFICATION = "bus_specification"
    ERRATA = "errata"


class TestFacet(StrEnum):
    SOURCE_TESTS = "source_tests"
    TEST_FRAMEWORK_DOCUMENTATION = "test_framework_documentation"


class ToolingFacet(StrEnum):
    TOOLCHAIN_DOCUMENTATION = "toolchain_documentation"
    PACKAGING_AND_RELEASE_DOCUMENTATION = "packaging_and_release_documentation"
    RUNTIME_DOCUMENTATION = "runtime_documentation"


class FacetDisposition(StrEnum):
    CONTROLLED = "CONTROLLED"
    EXPLICIT_GAP = "EXPLICIT_GAP"


class GapReason(StrEnum):
    NOT_FOUND = "NOT_FOUND"
    ACCESS_RESTRICTED = "ACCESS_RESTRICTED"
    LICENSE_UNCERTAIN = "LICENSE_UNCERTAIN"
    CONFLICTING_SOURCES = "CONFLICTING_SOURCES"
    UNAVAILABLE_PUBLIC_EVIDENCE = "UNAVAILABLE_PUBLIC_EVIDENCE"


class RetrievalOutcome(StrEnum):
    RETRIEVED = "RETRIEVED"
    NOT_FOUND = "NOT_FOUND"
    ACCESS_RESTRICTED = "ACCESS_RESTRICTED"
    LICENSE_UNCERTAIN = "LICENSE_UNCERTAIN"
    CONFLICT = "CONFLICT"
    FAILED = "FAILED"


class LocatorKind(StrEnum):
    GIT_BLOB = "git_blob"
    EXTERNAL_URL = "external_url"
    EXTERNAL_REFERENCE = "external_reference"


class MaterialOriginKind(StrEnum):
    GIT_BLOB = "git_blob"
    EXTERNAL_URL = "external_url"
    CARGO_REGISTRY = "cargo_registry"


class MaterialRedistribution(StrEnum):
    ALLOWED = "allowed"
    RESTRICTED = "restricted"
    UNKNOWN = "unknown"


FacetType = SourceFacet | TargetFacet | QemuFacet | HardwareFacet | TestFacet | ToolingFacet


@dataclass(frozen=True, slots=True)
class EvidenceFacet:
    lane: EvidenceLane
    name: FacetType

    def __post_init__(self) -> None:
        expected = _FACET_TYPES.get(self.lane)
        if expected is None or not isinstance(self.name, expected):
            raise WorkflowError(f"evidence lane {self.lane.value} does not own facet {self.name!r}")

    def to_dict(self) -> dict[str, str]:
        return {"lane": self.lane.value, "facet": self.name.value}

    @property
    def sort_key(self) -> tuple[str, str]:
        return self.lane.value, self.name.value


_FACET_TYPES = MappingProxyType(
    {
        EvidenceLane.SOURCE: SourceFacet,
        EvidenceLane.TARGET: TargetFacet,
        EvidenceLane.QEMU: QemuFacet,
        EvidenceLane.HARDWARE: HardwareFacet,
        EvidenceLane.TEST: TestFacet,
        EvidenceLane.TOOLING: ToolingFacet,
    }
)


def parse_facet(lane_value: object, facet_value: object) -> EvidenceFacet:
    try:
        lane = EvidenceLane(lane_value)
        facet = _FACET_TYPES[lane](facet_value)
    except (TypeError, ValueError) as error:
        raise WorkflowError(f"invalid evidence facet: {lane_value!r}/{facet_value!r}") from error
    return EvidenceFacet(lane, facet)


def required_facets() -> tuple[EvidenceFacet, ...]:
    return tuple(
        EvidenceFacet(lane, facet)
        for lane, facet_type in _FACET_TYPES.items()
        for facet in facet_type
    )


SOURCE_DRIVER_ENTRY = EvidenceFacet(EvidenceLane.SOURCE, SourceFacet.DRIVER_ENTRY)
SOURCE_DEPENDENCY_CLOSURE = EvidenceFacet(
    EvidenceLane.SOURCE,
    SourceFacet.DEPENDENCY_CLOSURE,
)
