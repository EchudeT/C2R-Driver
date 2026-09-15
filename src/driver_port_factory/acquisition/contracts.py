from enum import StrEnum


class AcquisitionStage(StrEnum):
    REVISION_SELECTION = "revision_selection"
    EVIDENCE_ACQUISITION = "evidence_acquisition"


class AcquisitionArtifact(StrEnum):
    REVISION_MANIFEST = "revision_manifest"
    ACQUISITION_PLAN = "acquisition_plan"
    ACQUISITION_ATTEMPT = "acquisition_attempt"
    ACQUISITION_MANIFEST = "acquisition_manifest"
    MATERIALS_MANIFEST = "materials_manifest"
    SOURCE_IDENTITY_VERIFICATION = "source_identity_verification"


class CoverageDisposition(StrEnum):
    ACQUIRED = "ACQUIRED"
    GAP = "GAP"


class AcquisitionEvidenceCategory(StrEnum):
    SOURCE = "source"
    TARGET = "target"
    QEMU = "qemu"
    HARDWARE = "hardware"
    TESTS = "tests"
    TOOLING = "tooling"


class AcquisitionAttemptOutcome(StrEnum):
    FAIL = "FAIL"
