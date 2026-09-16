"""Pre-acquisition request analysis and driver identity confirmation."""

from .catalog import DriverCandidate, DriverCatalog, MetadataSource, Resolution
from .resolver import (
    CompositeSourceDriverResolver,
    DriverMetadataProvider,
    DriverRequest,
    SourceDriverResolver,
)
from .service import IntakeAnalysisResult, IntakeService

__all__ = [
    "CompositeSourceDriverResolver",
    "DriverCandidate",
    "DriverCatalog",
    "DriverMetadataProvider",
    "DriverRequest",
    "IntakeAnalysisResult",
    "IntakeService",
    "MetadataSource",
    "Resolution",
    "SourceDriverResolver",
]
