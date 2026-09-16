from __future__ import annotations

from types import MappingProxyType

from ..core.models import WorkflowError
from ..core.validation import ArtifactValidator, json_object, utf8_document
from .contracts import (
    KnowledgeArtifact,
    KnowledgeEvidenceStatus,
    KnowledgeIndexStatus,
)


def _status(data: bytes) -> None:
    value = json_object(data, KnowledgeArtifact.STATUS.value)
    if KnowledgeIndexStatus(value.get("status")) is not KnowledgeIndexStatus.READY:
        raise WorkflowError("kb_status must be READY")
    if not isinstance(value.get("manifest_fingerprint"), str):
        raise WorkflowError("kb_status requires a manifest fingerprint")


def _query_contract(data: bytes) -> None:
    value = json_object(data, KnowledgeArtifact.QUERY_CONTRACT.value)
    commands = value.get("commands")
    if not isinstance(commands, dict) or set(commands) != {"status", "rebuild", "search", "show"}:
        raise WorkflowError("kb_query_contract requires all four query commands")
    if not value.get("template_sha256") or not value.get("manifest_sha256"):
        raise WorkflowError("kb_query_contract requires template and manifest hashes")


def _generated_skill(data: bytes) -> None:
    utf8_document(data)
    text = data.decode("utf-8")
    if "{{" in text or "}}" in text:
        raise WorkflowError("generated_kb_skill contains unresolved placeholders")
    if any(operation not in text for operation in ("status", "rebuild", "search", "show")):
        raise WorkflowError("generated_kb_skill does not describe every query operation")


def _probe_attempt(data: bytes) -> None:
    value = json_object(data, KnowledgeArtifact.PROBE_ATTEMPT.value)
    if KnowledgeEvidenceStatus(value.get("status")) is not KnowledgeEvidenceStatus.FAIL:
        raise WorkflowError("kb_probe_attempt must preserve a failed probe run")
    if not value.get("failed_probe_ids"):
        raise WorkflowError("kb_probe_attempt requires failed probe identifiers")


def _readiness(data: bytes) -> None:
    value = json_object(data, KnowledgeArtifact.READINESS_REPORT.value)
    if KnowledgeEvidenceStatus(value.get("status")) is not KnowledgeEvidenceStatus.PASS:
        raise WorkflowError("kb_readiness_report must pass")
    if value.get("failed_probe_ids"):
        raise WorkflowError("kb_readiness_report must have no failed probes")


def _target_probes(data: bytes) -> None:
    value = json_object(data, KnowledgeArtifact.TARGET_PROBE_RESULTS.value)
    probes = value.get("probes")
    if KnowledgeEvidenceStatus(value.get("status")) is not KnowledgeEvidenceStatus.PASS:
        raise WorkflowError("target_probe_results must pass")
    if not isinstance(probes, list) or not probes:
        raise WorkflowError("target_probe_results must contain probes")
    if any(
        KnowledgeEvidenceStatus(probe.get("status")) is not KnowledgeEvidenceStatus.PASS
        for probe in probes
    ):
        raise WorkflowError("every target knowledge probe must pass")


VALIDATORS = MappingProxyType[KnowledgeArtifact, ArtifactValidator](
    {
        KnowledgeArtifact.STATUS: _status,
        KnowledgeArtifact.QUERY_CONTRACT: _query_contract,
        KnowledgeArtifact.PROBE_ATTEMPT: _probe_attempt,
        KnowledgeArtifact.GENERATED_SKILL: _generated_skill,
        KnowledgeArtifact.READINESS_REPORT: _readiness,
        KnowledgeArtifact.TARGET_PROBE_RESULTS: _target_probes,
    }
)
