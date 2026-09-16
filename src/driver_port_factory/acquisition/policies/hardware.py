from ..facets import HardwareFacet
from .contracts import FacetOriginPolicy

_POLICY = FacetOriginPolicy(frozenset(), frozenset(), True)


def policy_for(facet: HardwareFacet) -> FacetOriginPolicy:
    return _POLICY
