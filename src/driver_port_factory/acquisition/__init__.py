"""Pinned repository acquisition and minimum evidence closure."""

from .closure import EvidenceClosureFinalizer, EvidenceClosureResult
from .job import ArtifactOccurrence
from .proposal import EvidenceProposalImporter, ProposalImport
from .repository import RepositoryAcquirer
from .repository_checkout import CheckoutRecord
from .repository_manifest import RepositoryAcquisition
from .repository_role import RepositoryRole
from .repository_spec import RepositorySpec
from .revision_manifest import RepositoryPlan
from .revision_selection import RevisionSelector
from .verification import AcquisitionVerifier

__all__ = [
    "AcquisitionVerifier",
    "ArtifactOccurrence",
    "CheckoutRecord",
    "EvidenceClosureFinalizer",
    "EvidenceClosureResult",
    "EvidenceProposalImporter",
    "ProposalImport",
    "RepositoryAcquirer",
    "RepositoryAcquisition",
    "RepositoryPlan",
    "RepositoryRole",
    "RepositorySpec",
    "RevisionSelector",
]
