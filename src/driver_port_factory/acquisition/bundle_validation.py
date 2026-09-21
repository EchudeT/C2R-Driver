from types import MappingProxyType

from ..core.validation import BundleValidator
from .contracts import AcquisitionStage
from .evidence_validation import validate_evidence_closure_bundle
from .repository_validation import validate_repository_bundle

BUNDLE_VALIDATORS = MappingProxyType[AcquisitionStage, BundleValidator](
    {
        AcquisitionStage.REPOSITORY_ACQUISITION: validate_repository_bundle,
        AcquisitionStage.EVIDENCE_CLOSURE: validate_evidence_closure_bundle,
    }
)
