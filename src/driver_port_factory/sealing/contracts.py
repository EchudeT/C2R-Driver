from enum import StrEnum


class SealingStage(StrEnum):
    CANDIDATE_SEALING = "candidate_sealing"
    OPAQUE_DIGEST_EXPORT = "opaque_digest_export"
    CANDIDATE_TRANSFER = "candidate_transfer"


class SealingArtifact(StrEnum):
    CANDIDATE_MANIFEST = "candidate_manifest"
    CANDIDATE_BUNDLE = "candidate_bundle"
    CANDIDATE_LEDGER_EVENT = "candidate_ledger_event"
    CANDIDATE_TIMESTAMP_RECEIPT = "candidate_timestamp_receipt"
    CANDIDATE_DIGEST_ANCHOR = "candidate_digest_anchor"
    CANDIDATE_TRANSFER_RECORD = "candidate_transfer_record"
    EVALUATOR_RECEIPT = "evaluator_receipt"


class SealingEvent(StrEnum):
    CANDIDATE_SEALED = "candidate.sealed"
    CANDIDATE_TRANSFERRED = "candidate.transferred"


class PrivateEvaluationState(StrEnum):
    NOT_RUN_BY_MIGRATOR = "NOT_RUN_BY_MIGRATOR"


class TimestampReceiptKind(StrEnum):
    WORM = "WORM"
    TRUSTED_TIMESTAMP = "TRUSTED_TIMESTAMP"


class CandidateBundleFormat(StrEnum):
    TAR = "TAR"
