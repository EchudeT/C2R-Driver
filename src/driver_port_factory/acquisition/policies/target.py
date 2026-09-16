from ..facets import TargetFacet
from ..repository_role import RepositoryRole
from .contracts import FacetOriginPolicy

_TARGET = frozenset({RepositoryRole.TARGET})
_POLICY = FacetOriginPolicy(_TARGET, _TARGET, False)


def policy_for(facet: TargetFacet) -> FacetOriginPolicy:
    return _POLICY
