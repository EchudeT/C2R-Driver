from ..facets import TestFacet
from ..repository_role import RepositoryRole
from .contracts import FacetOriginPolicy

_SOURCE = frozenset({RepositoryRole.SOURCE})
_SOURCE_AND_TARGET = frozenset({RepositoryRole.SOURCE, RepositoryRole.TARGET})


def policy_for(facet: TestFacet) -> FacetOriginPolicy:
    match facet:
        case TestFacet.SOURCE_TESTS:
            return FacetOriginPolicy(_SOURCE, _SOURCE, True)
        case TestFacet.TEST_FRAMEWORK_DOCUMENTATION:
            return FacetOriginPolicy(_SOURCE_AND_TARGET, _SOURCE_AND_TARGET, True)
    raise AssertionError(f"unmapped test evidence facet: {facet.value}")
