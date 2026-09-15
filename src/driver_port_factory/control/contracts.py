from enum import StrEnum


class ControlStage(StrEnum):
    PROJECT_INIT = "project_init"


class ControlArtifact(StrEnum):
    PROJECT_MANIFEST = "project_manifest"


class LedgerVerificationStatus(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
