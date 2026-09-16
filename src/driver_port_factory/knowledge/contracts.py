from enum import StrEnum

from ..core.models import WorkflowError


class KnowledgeDependencyClosureError(WorkflowError):
    """The frozen target dependency closure failed independently of a Codex proposal."""


class KnowledgeStage(StrEnum):
    KNOWLEDGE_BASE = "knowledge_base"


class KnowledgeArtifact(StrEnum):
    STATUS = "kb_status"
    QUERY_CONTRACT = "kb_query_contract"
    PROBE_ATTEMPT = "kb_probe_attempt"
    GENERATED_SKILL = "generated_kb_skill"
    READINESS_REPORT = "kb_readiness_report"
    TARGET_PROBE_RESULTS = "target_probe_results"


class KnowledgeIndexStatus(StrEnum):
    READY = "READY"


class KnowledgeEvidenceStatus(StrEnum):
    VERIFIED = "VERIFIED"
    INFERRED = "INFERRED"
    PLANNED = "PLANNED"
    NOT_RUN = "NOT_RUN"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    BLOCKED = "BLOCKED"
    FAIL = "FAIL"
    PASS = "PASS"


class KnowledgeDomain(StrEnum):
    HARDWARE = "hardware"
    SOURCE = "source"
    TARGET = "target"
    QEMU = "qemu"
    TOOLING = "tooling"
    TEST = "test"


class RequiredProbeTopic(StrEnum):
    REGISTRATION_LIFECYCLE = "registration-lifecycle"
    RESOURCES_IO_DMA = "resources-io-dma"
    INTERRUPTS_CONCURRENCY = "interrupts-concurrency"
    OWNERSHIP_ERRORS_RECOVERY = "ownership-errors-recovery"
    RUST_SAFETY_STYLE = "rust-safety-style"
    ANALOGOUS_DRIVER_FRAMEWORK = "analogous-driver-framework"
    ARTIFACT_PACKAGING_QEMU = "artifact-packaging-qemu"
    SOURCE_DRIVER_ENTRY = "source-driver-entry"
    QEMU_DEVICE_MODEL = "qemu-device-model"
    HARDWARE_OR_EXPLICIT_GAP = "hardware-or-explicit-gap"

    @property
    def domain(self) -> KnowledgeDomain:
        match self:
            case RequiredProbeTopic.SOURCE_DRIVER_ENTRY:
                return KnowledgeDomain.SOURCE
            case RequiredProbeTopic.QEMU_DEVICE_MODEL:
                return KnowledgeDomain.QEMU
            case RequiredProbeTopic.HARDWARE_OR_EXPLICIT_GAP:
                return KnowledgeDomain.HARDWARE
            case (
                RequiredProbeTopic.REGISTRATION_LIFECYCLE
                | RequiredProbeTopic.RESOURCES_IO_DMA
                | RequiredProbeTopic.INTERRUPTS_CONCURRENCY
                | RequiredProbeTopic.OWNERSHIP_ERRORS_RECOVERY
                | RequiredProbeTopic.RUST_SAFETY_STYLE
                | RequiredProbeTopic.ANALOGOUS_DRIVER_FRAMEWORK
                | RequiredProbeTopic.ARTIFACT_PACKAGING_QEMU
            ):
                return KnowledgeDomain.TARGET
        raise AssertionError(f"unmapped mandatory probe topic: {self.value}")
