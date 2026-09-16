from enum import StrEnum


class MigrationStage(StrEnum):
    HANDOFF = "migration_handoff"
    CONTRACTS = "migration_contracts"
    TEST_ADAPTATION = "test_adaptation"
    DRIVER_IMPLEMENTATION = "driver_implementation"
    TARGET_COMPLIANCE = "target_compliance"
    ARTIFACT_PREPARATION = "artifact_preparation"
    PUBLIC_QEMU_VALIDATION = "public_qemu_validation"
    PUBLIC_REPAIR = "public_repair"
    COMPLETION_AUDIT = "completion_audit"


class MigrationArtifact(StrEnum):
    HANDOFF = "migration_handoff"
    CONTRACTS = "migration_contracts"
    TEST_PORT_MATRIX = "test_port_matrix"
    IMPLEMENTATION_BUNDLE = "driver_implementation_bundle"
    TRANSLATION_COVERAGE = "translation_coverage"
    TARGET_CHANGE_INVENTORY = "target_change_inventory"
    COMPLIANCE_REPORT = "compliance_report"
    RUNTIME_ARTIFACT = "runtime_artifact"
    ARTIFACT_IDENTITY = "artifact_identity"
    PUBLIC_QEMU_REPORT = "public_qemu_report"
    PUBLIC_REPAIR_REPORT = "public_repair_report"
    EVIDENCE_AUDIT = "evidence_audit"


class HandoffMode(StrEnum):
    DEVELOPER = "DEVELOPER"
    BLIND_CANDIDATE = "BLIND_CANDIDATE"


class ContractEvidenceStatus(StrEnum):
    VERIFIED = "VERIFIED"
    INFERRED = "INFERRED"
    UNKNOWN = "UNKNOWN"


class ContractExecutionStatus(StrEnum):
    NOT_RUN = "NOT_RUN"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    BLOCKED = "BLOCKED"
    FAIL = "FAIL"
    PASS = "PASS"


class ContractVerificationKind(StrEnum):
    STATIC = "STATIC"
    QEMU = "QEMU"


class EvidenceSuccessor(StrEnum):
    KNOWLEDGE = "knowledge"
    ACQUISITION = "acquisition"


class TestPrimaryClass(StrEnum):
    DEVICE_FUNCTIONAL = "DEVICE_FUNCTIONAL"
    DEVICE_PROTOCOL_INTERNAL = "DEVICE_PROTOCOL_INTERNAL"
    PORTABLE_INTENT_PLATFORM_HARNESS = "PORTABLE_INTENT_PLATFORM_HARNESS"
    SOURCE_PLATFORM_SEMANTICS = "SOURCE_PLATFORM_SEMANTICS"
    OUT_OF_SCOPE_DEVICE_VARIANT = "OUT_OF_SCOPE_DEVICE_VARIANT"
    TARGET_CAPABILITY_BLOCKED = "TARGET_CAPABILITY_BLOCKED"
    QEMU_MODEL_BLOCKED = "QEMU_MODEL_BLOCKED"


class TestDisposition(StrEnum):
    RETAIN = "RETAIN"
    ADAPT = "ADAPT"
    EXCLUDE = "EXCLUDE"
    PRESERVE_BLOCKED = "PRESERVE_BLOCKED"


class TestOrigin(StrEnum):
    SOURCE_TEST = "SOURCE_TEST"
    NEW_MIGRATION_TEST = "NEW_MIGRATION_TEST"


class ImplementationFileRole(StrEnum):
    DRIVER = "DRIVER"
    PUBLIC_TEST = "PUBLIC_TEST"
    INTEGRATION = "INTEGRATION"


class TranslationDomain(StrEnum):
    FUNCTION_CALLBACK = "FUNCTION_CALLBACK"
    TYPE_LAYOUT = "TYPE_LAYOUT"
    GLOBAL_STATE = "GLOBAL_STATE"
    HARDWARE_EFFECT = "HARDWARE_EFFECT"
    LIFECYCLE_ERROR = "LIFECYCLE_ERROR"
    TEST_ASSERTION = "TEST_ASSERTION"


class TranslationStatus(StrEnum):
    TRANSLATED = "TRANSLATED"
    PARTIAL = "PARTIAL"
    BLOCKED = "BLOCKED"
    UNMAPPED = "UNMAPPED"
    UNSAFE_REQUIRED = "UNSAFE_REQUIRED"


class SourceFactKind(StrEnum):
    FUNCTION_DEFINITION = "FUNCTION_DEFINITION"
    RECORD_DEFINITION = "RECORD_DEFINITION"
    GLOBAL = "GLOBAL"
    EFFECT = "EFFECT"
    CALL = "CALL"
    CONTROL_FLOW = "CONTROL_FLOW"


class TargetChangeStatus(StrEnum):
    PLANNED = "TARGET_CHANGE_PLANNED"
    VERIFIED = "TARGET_CHANGE_VERIFIED"
    BLOCKED = "BLOCKED_TARGET_CHANGE"
