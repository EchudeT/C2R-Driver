from ..facets import QemuFacet
from ..repository_role import RepositoryRole
from .contracts import FacetOriginPolicy

_QEMU = frozenset({RepositoryRole.QEMU})
_POLICY = FacetOriginPolicy(_QEMU, _QEMU, False)


def policy_for(facet: QemuFacet) -> FacetOriginPolicy:
    return _POLICY
