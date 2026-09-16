from enum import StrEnum


class AcquisitionStage(StrEnum):
    REVISION_SELECTION = "revision_selection"
    REPOSITORY_ACQUISITION = "repository_acquisition"
    EVIDENCE_CLOSURE = "evidence_closure"


class AcquisitionArtifact(StrEnum):
    REVISION_SELECTION_PROPOSAL = "revision_selection_proposal"
    REVISION_EVIDENCE_CONTENT = "revision_evidence_content"
    REVISION_MANIFEST = "revision_manifest"
    REPOSITORY_PLAN = "repository_plan"
    REPOSITORY_MANIFEST = "repository_manifest"
    REPOSITORY_ACQUISITION_ATTEMPT = "repository_acquisition_attempt"
    SOURCE_IDENTITY_VERIFICATION = "source_identity_verification"
    EVIDENCE_DISCOVERY_PROPOSAL = "evidence_discovery_proposal"
    EVIDENCE_CLOSURE_PLAN = "evidence_closure_plan"
    MATERIALS_MANIFEST = "materials_manifest"
    EVIDENCE_COVERAGE_INVENTORY = "evidence_coverage_inventory"
    EVIDENCE_GAP_REGISTER = "evidence_gap_register"
    EVIDENCE_RETRIEVAL_LEDGER = "evidence_retrieval_ledger"
    EVIDENCE_HTTP_CONTENT = "evidence_http_content"


class RepositoryAcquisitionAttemptOutcome(StrEnum):
    FAIL = "FAIL"
