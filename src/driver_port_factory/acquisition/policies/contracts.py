from dataclasses import dataclass

from ..repository_role import RepositoryRole


@dataclass(frozen=True, slots=True)
class FacetOriginPolicy:
    git_roles: frozenset[RepositoryRole]
    endorsement_roles: frozenset[RepositoryRole]
    corroborated_external: bool
    empty_inventory_allowed: bool = False
