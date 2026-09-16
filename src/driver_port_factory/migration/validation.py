from types import MappingProxyType

from ..core.validation import (
    ArtifactValidator,
    BundleValidator,
    json_object_document,
    nonempty,
    utf8_document,
)
from .contract_set import validate_contract_bundle
from .contracts import MigrationArtifact, MigrationStage
from .handoff import validate_handoff_bundle

VALIDATORS = MappingProxyType[MigrationArtifact, ArtifactValidator](
    {
        MigrationArtifact.HANDOFF: json_object_document,
        MigrationArtifact.CONTRACTS: json_object_document,
        MigrationArtifact.TEST_PORT_MATRIX: json_object_document,
        MigrationArtifact.RUST_DESIGN: utf8_document,
        MigrationArtifact.DRIVER_SOURCE: utf8_document,
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
    }
)
