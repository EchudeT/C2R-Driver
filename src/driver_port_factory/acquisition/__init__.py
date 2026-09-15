"""Pinned, provenance-tracked source/target/QEMU acquisition."""

from .execution import AcquisitionResult, EvidenceAcquirer
from .models import AcquisitionPlan, CheckoutRecord, RepositoryRole, RepositorySpec
from .planning import AcquisitionPlanner
from .verification import AcquisitionVerifier

__all__ = [
    "AcquisitionPlan",
    "AcquisitionPlanner",
    "AcquisitionResult",
    "AcquisitionVerifier",
    "CheckoutRecord",
    "EvidenceAcquirer",
    "RepositoryRole",
    "RepositorySpec",
]
