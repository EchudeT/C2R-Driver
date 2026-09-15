from __future__ import annotations

import json
from types import MappingProxyType

from ..core.models import WorkflowError
from ..core.validation import ArtifactValidator, json_object
from ..knowledge.contracts import KnowledgeDomain, MaterialRedistribution
from .contracts import (
    AcquisitionArtifact,
    AcquisitionAttemptOutcome,
    AcquisitionEvidenceCategory,
    CoverageDisposition,
)
from .models import RepositoryRole


def _revision_manifest(data: bytes) -> None:
    value = json_object(data, AcquisitionArtifact.REVISION_MANIFEST.value)
    for role in RepositoryRole:
        entry = value.get(role.value)
        if not isinstance(entry, dict) or not entry.get("revision"):
            raise WorkflowError(f"revision_manifest.{role.value}.revision must be pinned")


def _acquisition_plan(data: bytes) -> None:
    value = json_object(data, AcquisitionArtifact.ACQUISITION_PLAN.value)
    repositories = value.get("repositories")
    if not isinstance(repositories, list):
        raise WorkflowError("acquisition_plan.repositories must be a list")
    roles = {entry.get("role") for entry in repositories if isinstance(entry, dict)}
    if roles != {role.value for role in RepositoryRole}:
        raise WorkflowError("acquisition_plan requires source, target, and qemu repositories")
    if any(len(str(entry.get("resolved_commit", ""))) not in {40, 64} for entry in repositories):
        raise WorkflowError("each acquisition repository needs a full Git commit")


def _acquisition_manifest(data: bytes) -> None:
    value = json_object(data, AcquisitionArtifact.ACQUISITION_MANIFEST.value)
    checkouts = value.get("checkouts")
    if not isinstance(checkouts, list) or len(checkouts) != len(RepositoryRole):
        raise WorkflowError("acquisition_manifest requires three controlled checkouts")
    if not isinstance(value.get("source_identity_verification"), dict):
        raise WorkflowError("acquisition_manifest requires source identity verification")
    coverage = value.get("coverage_inventory")
    expected = {
        AcquisitionEvidenceCategory.SOURCE: CoverageDisposition.ACQUIRED,
        AcquisitionEvidenceCategory.TARGET: CoverageDisposition.ACQUIRED,
        AcquisitionEvidenceCategory.QEMU: CoverageDisposition.ACQUIRED,
        AcquisitionEvidenceCategory.HARDWARE: CoverageDisposition.GAP,
        AcquisitionEvidenceCategory.TESTS: CoverageDisposition.GAP,
        AcquisitionEvidenceCategory.TOOLING: CoverageDisposition.GAP,
    }
    if not isinstance(coverage, dict) or set(coverage) != {category.value for category in expected}:
        raise WorkflowError("acquisition_manifest has an incomplete coverage inventory")
    try:
        actual = {category: CoverageDisposition(coverage[category.value]) for category in expected}
    except (TypeError, ValueError) as error:
        raise WorkflowError("acquisition_manifest has an invalid coverage disposition") from error
    if actual != expected:
        raise WorkflowError("acquisition_manifest coverage contradicts the acquisition gate")


def _materials_manifest(data: bytes) -> None:
    try:
        lines = [json.loads(line) for line in data.decode("utf-8").splitlines() if line]
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise WorkflowError("materials_manifest must be JSON Lines") from error
    if not lines or any(
        not isinstance(line, dict) or "sha256" not in line or "revision" not in line
        for line in lines
    ):
        raise WorkflowError("materials_manifest entries require sha256 and revision")
    try:
        for line in lines:
            KnowledgeDomain(line.get("domain"))
            MaterialRedistribution(line.get("redistribution"))
    except (TypeError, ValueError) as error:
        raise WorkflowError("materials_manifest has invalid domain or redistribution") from error


def _source_identity(data: bytes) -> None:
    value = json_object(data, AcquisitionArtifact.SOURCE_IDENTITY_VERIFICATION.value)
    if not isinstance(value.get("consistent"), bool):
        raise WorkflowError("source_identity_verification.consistent must be boolean")


def _acquisition_attempt(data: bytes) -> None:
    value = json_object(data, AcquisitionArtifact.ACQUISITION_ATTEMPT.value)
    if AcquisitionAttemptOutcome(value.get("status")) is not AcquisitionAttemptOutcome.FAIL:
        raise WorkflowError("acquisition_attempt must preserve a failed attempt")
    if not value.get("failure_type") or not value.get("message"):
        raise WorkflowError("acquisition_attempt requires failure type and message")


VALIDATORS = MappingProxyType[AcquisitionArtifact, ArtifactValidator](
    {
        AcquisitionArtifact.REVISION_MANIFEST: _revision_manifest,
        AcquisitionArtifact.ACQUISITION_PLAN: _acquisition_plan,
        AcquisitionArtifact.ACQUISITION_ATTEMPT: _acquisition_attempt,
        AcquisitionArtifact.ACQUISITION_MANIFEST: _acquisition_manifest,
        AcquisitionArtifact.MATERIALS_MANIFEST: _materials_manifest,
        AcquisitionArtifact.SOURCE_IDENTITY_VERIFICATION: _source_identity,
    }
)
