from enum import StrEnum


class IntakeStage(StrEnum):
    REQUEST = "request_intake"
    CANDIDATE_RESOLUTION = "driver_candidate_resolution"
    SCOPE_CONFIRMATION = "scope_confirmation"
    ENVELOPE_FREEZE = "migration_envelope_freeze"


class IntakeArtifact(StrEnum):
    REQUEST_RECORD = "request_record"
    DRIVER_CANDIDATES = "driver_candidates"
    CONFIRMATION_QUESTION = "confirmation_question"
    SCOPE_CONFIRMATION = "scope_confirmation"
    MIGRATION_ENVELOPE = "migration_envelope"
    IDENTITY_RECORD = "identity_record"


class IntakeStatus(StrEnum):
    UNRESOLVED = "UNRESOLVED"
    ANALYZING = "ANALYZING"
    NEEDS_USER_CONFIRMATION = "NEEDS_USER_CONFIRMATION"
    WAITING_FOR_USER = "WAITING_FOR_USER"
    CONFIRMED = "CONFIRMED"
    FROZEN = "FROZEN"


class MetadataScope(StrEnum):
    LIGHTWEIGHT_ONLY = "LIGHTWEIGHT_ONLY"


class ResolutionMatch(StrEnum):
    EXACT = "EXACT"
    FUZZY = "FUZZY"
    NO_MATCH = "NO_MATCH"
