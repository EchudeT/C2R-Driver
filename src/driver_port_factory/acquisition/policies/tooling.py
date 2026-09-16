from ..facets import ToolingFacet
from ..repository_role import RepositoryRole
from .contracts import FacetOriginPolicy

_ALL_REPOSITORIES = frozenset(RepositoryRole)
_POLICY = FacetOriginPolicy(_ALL_REPOSITORIES, _ALL_REPOSITORIES, True)


def policy_for(facet: ToolingFacet) -> FacetOriginPolicy:
    return _POLICY
