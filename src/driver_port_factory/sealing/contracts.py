from enum import StrEnum


class SealingStage(StrEnum):
    CANDIDATE_SEALING = "candidate_sealing"
    OPAQUE_DIGEST_EXPORT = "opaque_digest_export"
    CANDIDATE_TRANSFER = "candidate_transfer"


class SealingArtifact(StrEnum):
    CANDIDATE_MANIFEST = "candidate_manifest"
    CANDIDATE_DIGEST_ANCHOR = "candidate_digest_anchor"
    CANDIDATE_TRANSFER_RECORD = "candidate_transfer_record"
