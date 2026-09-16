from enum import StrEnum


class MigrationStage(StrEnum):
    HANDOFF = "migration_handoff"
    CONTRACTS = "migration_contracts"
    TEST_ADAPTATION = "test_adaptation"
    RUST_DESIGN = "rust_design"
    RUST_IMPLEMENTATION = "rust_implementation"
    TARGET_COMPLIANCE = "target_compliance"
    ARTIFACT_PREPARATION = "artifact_preparation"
    PUBLIC_QEMU_VALIDATION = "public_qemu_validation"
    PUBLIC_REPAIR = "public_repair"
    COMPLETION_AUDIT = "completion_audit"


class MigrationArtifact(StrEnum):
    HANDOFF = "migration_handoff"
    CONTRACTS = "migration_contracts"
    TEST_PORT_MATRIX = "test_port_matrix"
    RUST_DESIGN = "rust_design"
    DRIVER_SOURCE = "driver_source"
    COMPLIANCE_REPORT = "compliance_report"
    RUNTIME_ARTIFACT = "runtime_artifact"
    ARTIFACT_IDENTITY = "artifact_identity"
    PUBLIC_QEMU_REPORT = "public_qemu_report"
    PUBLIC_REPAIR_REPORT = "public_repair_report"
    EVIDENCE_AUDIT = "evidence_audit"


class HandoffMode(StrEnum):
    DEVELOPER = "DEVELOPER"
    BLIND_CANDIDATE = "BLIND_CANDIDATE"
