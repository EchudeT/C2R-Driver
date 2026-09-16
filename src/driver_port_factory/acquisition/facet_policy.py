from __future__ import annotations

from typing import cast

from ..core.models import WorkflowError
from .authority import AuthorityBasis, RepositoryEndorsementAuthority
from .facets import (
    EvidenceFacet,
    EvidenceLane,
    HardwareFacet,
    QemuFacet,
    SourceFacet,
    TargetFacet,
    TestFacet,
    ToolingFacet,
)
from .locators import (
    EvidenceLocator,
    ExternalReferenceLocator,
    ExternalUrlLocator,
    GitBlobLocator,
)
from .policies import hardware, qemu, source, target, test, tooling
from .policies.contracts import FacetOriginPolicy
from .repository_role import RepositoryRole


def policy_for(facet: EvidenceFacet) -> FacetOriginPolicy:
    match facet.lane:
        case EvidenceLane.SOURCE:
            return source.policy_for(cast(SourceFacet, facet.name))
        case EvidenceLane.TARGET:
            return target.policy_for(cast(TargetFacet, facet.name))
        case EvidenceLane.QEMU:
            return qemu.policy_for(cast(QemuFacet, facet.name))
        case EvidenceLane.HARDWARE:
            return hardware.policy_for(cast(HardwareFacet, facet.name))
        case EvidenceLane.TEST:
            return test.policy_for(cast(TestFacet, facet.name))
        case EvidenceLane.TOOLING:
            return tooling.policy_for(cast(ToolingFacet, facet.name))
    raise AssertionError(f"unmapped evidence lane: {facet.lane.value}")


def validate_locator_authority(facet: EvidenceFacet, locator: EvidenceLocator) -> None:
    policy = policy_for(facet)
    if isinstance(locator, GitBlobLocator):
        if locator.repository not in policy.git_roles:
            raise WorkflowError(
                f"{locator.repository.value} repository cannot control "
                f"{facet.lane.value}/{facet.name.value}"
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


def empty_locator_inventory_allowed(facet: EvidenceFacet) -> bool:
    return policy_for(facet).empty_inventory_allowed


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
                f"{facet.lane.value}/{facet.name.value}"
            )
        return
    if not policy.corroborated_external:
        raise WorkflowError(
            f"corroborated external evidence cannot control {facet.lane.value}/{facet.name.value}"
        )
