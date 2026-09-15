from __future__ import annotations

from types import MappingProxyType

from ..core.models import WorkflowError
from ..core.validation import ArtifactValidator, json_object
from .contracts import SealingArtifact


def _candidate_manifest(data: bytes) -> None:
    value = json_object(data, SealingArtifact.CANDIDATE_MANIFEST.value)
    if value.get("schema_version") != 1 or not isinstance(value.get("artifacts"), list):
        raise WorkflowError("candidate_manifest requires schema_version=1 and artifacts")


def _digest_anchor(data: bytes) -> None:
    value = json_object(data, SealingArtifact.CANDIDATE_DIGEST_ANCHOR.value)
    digest = value.get("candidate_sha256")
    if not isinstance(digest, str) or len(digest) != 64:
        raise WorkflowError("candidate_digest_anchor.candidate_sha256 must be SHA256")


def _transfer_record(data: bytes) -> None:
    value = json_object(data, SealingArtifact.CANDIDATE_TRANSFER_RECORD.value)
    if not value.get("candidate_sha256") or not value.get("transferred_at"):
        raise WorkflowError("candidate_transfer_record requires digest and transfer time")


VALIDATORS = MappingProxyType[SealingArtifact, ArtifactValidator](
    {
        SealingArtifact.CANDIDATE_MANIFEST: _candidate_manifest,
        SealingArtifact.CANDIDATE_DIGEST_ANCHOR: _digest_anchor,
        SealingArtifact.CANDIDATE_TRANSFER_RECORD: _transfer_record,
    }
)
