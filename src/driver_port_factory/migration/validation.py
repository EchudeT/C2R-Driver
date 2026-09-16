from types import MappingProxyType

from ..core.validation import (
    ArtifactValidator,
    BundleValidator,
    json_object_document,
    nonempty,
)
from .compliance import validate_compliance_bundle
from .contract_set import validate_contract_bundle
from .contracts import MigrationArtifact, MigrationStage
from .handoff import validate_handoff_bundle
from .implementation import validate_implementation_bundle
from .test_matrix import validate_test_matrix_bundle

VALIDATORS = MappingProxyType[MigrationArtifact, ArtifactValidator](
    {
        MigrationArtifact.HANDOFF: json_object_document,
        MigrationArtifact.CONTRACTS: json_object_document,
        MigrationArtifact.TEST_PORT_MATRIX: json_object_document,
        MigrationArtifact.IMPLEMENTATION_BUNDLE: json_object_document,
        MigrationArtifact.TRANSLATION_COVERAGE: json_object_document,
        MigrationArtifact.TARGET_CHANGE_INVENTORY: json_object_document,
        MigrationArtifact.COMPLIANCE_REPORT: json_object_document,
        MigrationArtifact.RUNTIME_ARTIFACT: nonempty,
        MigrationArtifact.ARTIFACT_IDENTITY: json_object_document,
        MigrationArtifact.PUBLIC_QEMU_REPORT: json_object_document,
        MigrationArtifact.PUBLIC_REPAIR_REPORT: json_object_document,
        MigrationArtifact.EVIDENCE_AUDIT: json_object_document,
    }
)

BUNDLE_VALIDATORS = MappingProxyType[MigrationStage, BundleValidator](
    {
        MigrationStage.HANDOFF: validate_handoff_bundle,
        MigrationStage.CONTRACTS: validate_contract_bundle,
        MigrationStage.TEST_ADAPTATION: validate_test_matrix_bundle,
        MigrationStage.DRIVER_IMPLEMENTATION: validate_implementation_bundle,
        MigrationStage.TARGET_COMPLIANCE: validate_compliance_bundle,
    }
)
