"""Pinned, provenance-tracked source/target/QEMU acquisition."""

from .models import AcquisitionPlan, CheckoutRecord, RepositoryRole, RepositorySpec
from .service import AcquisitionResult, AcquisitionService

__all__ = [
    "AcquisitionPlan",
    "AcquisitionResult",
    "AcquisitionService",
    "CheckoutRecord",
    "RepositoryRole",
    "RepositorySpec",
]
