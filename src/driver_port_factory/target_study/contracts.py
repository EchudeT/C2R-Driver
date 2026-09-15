from enum import StrEnum


class TargetStudyStage(StrEnum):
    STUDY = "target_platform_study"


class TargetStudyArtifact(StrEnum):
    PROFILE = "target_profile"
    STRUCTURED_PROFILE = "target_profile_structured"
    API_EVIDENCE = "target_api_evidence"
    ANALOGOUS_DRIVER_TRACE = "analogous_driver_trace"
    CHANGE_PLAN = "target_change_plan"
    REPORT = "target_study_report"
    VALIDATION_ATTEMPT = "target_study_validation_attempt"


class TargetStudyOutcome(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"


class TargetProfileStatus(StrEnum):
    READY = "READY"


class BaselineStatus(StrEnum):
    PASS = "PASS"
    NOT_RUN = "NOT_RUN"
    BLOCKED = "BLOCKED"


class ApiConfidence(StrEnum):
    VERIFIED = "VERIFIED"
    INFERRED = "INFERRED"
    UNKNOWN = "UNKNOWN"


class TraceStatus(StrEnum):
    VERIFIED = "VERIFIED"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class ChangeLevel(StrEnum):
    DRIVER_OWNED = "driver-owned"
    INTEGRATION_WIRING = "integration-wiring"
    TARGET_API_FRAMEWORK = "target-api-framework"


class InvestigationStatus(StrEnum):
    PLANNED = "PLANNED"
    BLOCKED = "BLOCKED"
    PASS = "PASS"


class ApprovalStatus(StrEnum):
    APPROVED = "APPROVED"
