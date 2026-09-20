from __future__ import annotations

from dataclasses import dataclass

from ..core.models import WorkflowError
from .authority import AuthorityBasis, RepositoryEndorsementAuthority
from .facets import (
    EvidenceFacet,
    EvidenceLane,
    SourceFacet,
    TestFacet,
)
from .locators import (
    EvidenceLocator,
    ExternalReferenceLocator,
    ExternalUrlLocator,
    GitBlobLocator,
)
from .repository_role import RepositoryRole


@dataclass(frozen=True, slots=True)
class FacetOriginPolicy:
    git_roles: frozenset[RepositoryRole]
    endorsement_roles: frozenset[RepositoryRole]
    corroborated_external: bool


def policy_for(facet: EvidenceFacet) -> FacetOriginPolicy:
    source = frozenset({RepositoryRole.SOURCE})
    target = frozenset({RepositoryRole.TARGET})
    qemu = frozenset({RepositoryRole.QEMU})
    match facet.lane:
        case EvidenceLane.SOURCE:
            endorsements = frozenset() if facet.name == SourceFacet.DRIVER_ENTRY else source
            return FacetOriginPolicy(source, endorsements, False)
        case EvidenceLane.TARGET:
            return FacetOriginPolicy(target, target, False)
        case EvidenceLane.QEMU:
            return FacetOriginPolicy(qemu, qemu, False)
        case EvidenceLane.HARDWARE:
            return FacetOriginPolicy(frozenset(), frozenset(), True)
        case EvidenceLane.TEST:
            roles = (
                source
                if facet.name == TestFacet.SOURCE_TESTS
                else frozenset(RepositoryRole)
            )
            return FacetOriginPolicy(roles, roles, True)
        case EvidenceLane.TOOLING:
            roles = frozenset(RepositoryRole)
            return FacetOriginPolicy(roles, roles, True)
    raise AssertionError(f"unmapped evidence lane: {facet.lane.value}")


def validate_locator_authority(facet: EvidenceFacet, locator: EvidenceLocator) -> None:
    policy = policy_for(facet)
    if isinstance(locator, GitBlobLocator):
        if locator.repository not in policy.git_roles:
            remedy = (
                "Hardware facets must omit repository_paths, even when declaring a gap. "
                "Keep source/QEMU implementation evidence in its own lane; for missing "
                "primary hardware evidence use an actually checked external_urls entry "
                "and gap with impact and repair_trigger. Do not invent URLs."
                if facet.lane is EvidenceLane.HARDWARE else
                "Use a repository allowed for this lane: "
                + ", ".join(sorted(role.value for role in policy.git_roles))
                + ". Classify by evidence authority, not the topic mentioned in the file."
            )
            raise WorkflowError(
                f"{locator.repository.value} repository cannot control "
                f"{facet.lane.value}/{facet.name}: {locator.path}. {remedy}"
            )
        return
    if isinstance(locator, ExternalReferenceLocator):
        return
    _validate_external(facet, locator, policy)


def repository_is_authoritative(facet: EvidenceFacet, role: RepositoryRole) -> bool:
    return role in policy_for(facet).git_roles


def external_authority_is_allowed(facet: EvidenceFacet, authority: AuthorityBasis) -> bool:
    policy = policy_for(facet)
    if isinstance(authority, RepositoryEndorsementAuthority):
        return authority.repository in policy.endorsement_roles
    return policy.corroborated_external


def _validate_external(
    facet: EvidenceFacet,
    locator: ExternalUrlLocator,
    policy: FacetOriginPolicy,
) -> None:
    authority = locator.authority
    if isinstance(authority, RepositoryEndorsementAuthority):
        if authority.repository not in policy.endorsement_roles:
            raise WorkflowError(
                f"{authority.repository.value} repository endorsement cannot control "
                f"{facet.lane.value}/{facet.name}"
            )
        return
    if not policy.corroborated_external:
        raise WorkflowError(
            f"corroborated external evidence cannot control {facet.lane.value}/{facet.name}"
        )
