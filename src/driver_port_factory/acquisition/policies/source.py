from ..facets import SourceFacet
from ..repository_role import RepositoryRole
from .contracts import FacetOriginPolicy

_SOURCE = frozenset({RepositoryRole.SOURCE})
_IN_REPOSITORY = FacetOriginPolicy(_SOURCE, _SOURCE, False)
_FROZEN_BLOB_ONLY = FacetOriginPolicy(_SOURCE, frozenset(), False)
_INITIAL_DEPENDENCIES = FacetOriginPolicy(_SOURCE, frozenset(), False, True)


def policy_for(facet: SourceFacet) -> FacetOriginPolicy:
    match facet:
        case SourceFacet.DRIVER_ENTRY:
            return _FROZEN_BLOB_ONLY
        case SourceFacet.DEPENDENCY_CLOSURE:
            return _INITIAL_DEPENDENCIES
        case SourceFacet.FRAMEWORK_CONTRACTS | SourceFacet.BUILD_CONFIGURATION:
            return _IN_REPOSITORY
    raise AssertionError(f"unmapped source evidence facet: {facet.value}")
