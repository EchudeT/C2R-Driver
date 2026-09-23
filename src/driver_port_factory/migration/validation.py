from types import MappingProxyType

from ..core.validation import (
    ArtifactValidator,
    BundleValidator,
    json_object_document,
    nonempty,
    utf8_document,
)
from .artifact_preparation import validate_artifact_bundle
from .completion_audit import validate_completion_audit_bundle
from .contracts import MigrationArtifact, MigrationStage
from .handoff import validate_handoff_bundle
from .implementation import validate_implementation_bundle
from .public_qemu import validate_public_qemu_bundle
from .public_repair import validate_public_repair_bundle
from .analysis_review import validate_analysis_review

VALIDATORS = MappingProxyType[MigrationArtifact, ArtifactValidator](
    {
        MigrationArtifact.HANDOFF: json_object_document,
        MigrationArtifact.CONTRACTS: utf8_document,
        MigrationArtifact.TEST_PORT_MATRIX: utf8_document,
        MigrationArtifact.ANALYSIS_REVIEW_REPORT: json_object_document,
        MigrationArtifact.IMPLEMENTATION_BUNDLE: json_object_document,
        MigrationArtifact.COMPLIANCE_REPORT: utf8_document,
        MigrationArtifact.TARGET_CHANGE_INVENTORY: json_object_document,
        MigrationArtifact.ARTIFACT_PREPARATION_ATTEMPT: json_object_document,
        MigrationArtifact.RUNTIME_ARTIFACT: nonempty,
        MigrationArtifact.RUNTIME_VARIANT: nonempty,
        MigrationArtifact.ARTIFACT_IDENTITY: json_object_document,
        MigrationArtifact.PUBLIC_QEMU_ATTEMPT: json_object_document,
        MigrationArtifact.PUBLIC_QEMU_REPORT: json_object_document,
        MigrationArtifact.PUBLIC_QEMU_WORK_REPORT: utf8_document,
        MigrationArtifact.PUBLIC_REPAIR_REPORT: json_object_document,
        MigrationArtifact.EVIDENCE_AUDIT: json_object_document,
    }
)

BUNDLE_VALIDATORS = MappingProxyType[MigrationStage, BundleValidator](
    {
        MigrationStage.HANDOFF: validate_handoff_bundle,
        MigrationStage.ANALYSIS_REVIEW: validate_analysis_review,
        MigrationStage.DRIVER_IMPLEMENTATION: validate_implementation_bundle,
        MigrationStage.ARTIFACT_PREPARATION: validate_artifact_bundle,
        MigrationStage.PUBLIC_QEMU_VALIDATION: validate_public_qemu_bundle,
        MigrationStage.PUBLIC_REPAIR: validate_public_repair_bundle,
        MigrationStage.COMPLETION_AUDIT: validate_completion_audit_bundle,
    }
)
