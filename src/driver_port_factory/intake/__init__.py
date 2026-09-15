"""Pre-acquisition request analysis and driver identity confirmation."""

from .catalog import DriverCandidate, DriverCatalog, MetadataSource, Resolution
from .resolver import (
    CompositeSourceDriverResolver,
    DriverMetadataProvider,
    DriverRequest,
    PinnedSourceIdentityVerifier,
    SourceDriverResolver,
    SourceEntryVerifier,
    SourceIdentityVerification,
)
from .service import IntakeAnalysisResult, IntakeService

__all__ = [
    "DriverCandidate",
    "DriverCatalog",
    "DriverMetadataProvider",
    "DriverRequest",
    "IntakeAnalysisResult",
    "IntakeService",
    "MetadataSource",
    "PinnedSourceIdentityVerifier",
    "Resolution",
    "SourceDriverResolver",
    "SourceEntryVerifier",
    "SourceIdentityVerification",
    "CompositeSourceDriverResolver",
]
