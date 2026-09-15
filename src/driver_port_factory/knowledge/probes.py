from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..core.models import WorkflowError
from .contracts import KnowledgeDomain, RequiredProbeTopic


@dataclass(frozen=True, slots=True)
class KnowledgeProbe:
    probe_id: str
    topic: str
    domain: KnowledgeDomain
    query: str
    required: bool
    expected_record_ids: tuple[str, ...]
    path_prefix: str | None
    limit: int

    @classmethod
    def parse(cls, value: Any) -> KnowledgeProbe:
        if not isinstance(value, dict):
            raise WorkflowError("each knowledge probe must be an object")
        required_fields = {"probe_id", "topic", "domain", "query", "required"}
        missing = sorted(required_fields - value.keys())
        if missing:
            raise WorkflowError(f"knowledge probe missing fields: {', '.join(missing)}")
        probe_id = cls._nonempty(value["probe_id"], "probe_id")
        topic = cls._nonempty(value["topic"], "topic")
        query = cls._nonempty(value["query"], "query")
        if not isinstance(value["required"], bool):
            raise WorkflowError("knowledge probe required must be boolean")
        try:
            domain = KnowledgeDomain(value["domain"])
        except (TypeError, ValueError) as error:
            raise WorkflowError(
                f"knowledge probe has unsupported domain: {value['domain']}"
            ) from error
        expected_ids = value.get("expected_record_ids", [])
        if not isinstance(expected_ids, list) or not all(
            isinstance(identifier, str) and identifier for identifier in expected_ids
        ):
            raise WorkflowError("knowledge probe expected_record_ids must be a string list")
        path_prefix = value.get("path_prefix")
        if path_prefix is not None and not isinstance(path_prefix, str):
            raise WorkflowError("knowledge probe path_prefix must be a string or null")
        limit = value.get("limit", 10)
        if not isinstance(limit, int) or not 1 <= limit <= 100:
            raise WorkflowError("knowledge probe limit must be between 1 and 100")
        return cls(
            probe_id,
            topic,
            domain,
            query,
            value["required"],
            tuple(expected_ids),
            path_prefix,
            limit,
        )

    @staticmethod
    def _nonempty(value: Any, field: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise WorkflowError(f"knowledge probe {field} must be non-empty")
        return value

    @property
    def required_topic(self) -> RequiredProbeTopic | None:
        if not self.required:
            return None
        try:
            return RequiredProbeTopic(self.topic)
        except ValueError:
            return None


@dataclass(frozen=True, slots=True)
class KnowledgeProbePlan:
    probes: tuple[KnowledgeProbe, ...]

    @classmethod
    def load(cls, path: Path) -> KnowledgeProbePlan:
        resolved = path.resolve()
        try:
            value = json.loads(resolved.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            raise WorkflowError(f"invalid knowledge probe plan: {resolved}") from error
        if not isinstance(value, dict) or value.get("schema_version") != 1:
            raise WorkflowError("knowledge probe plan must be a schema_version=1 object")
        records = value.get("probes")
        if not isinstance(records, list) or not records:
            raise WorkflowError("knowledge probe plan requires a non-empty probes list")
        probes = tuple(KnowledgeProbe.parse(record) for record in records)
        cls._validate_unique_ids(probes)
        cls._validate_required_topics(probes)
        return cls(probes)

    @staticmethod
    def _validate_unique_ids(probes: tuple[KnowledgeProbe, ...]) -> None:
        identifiers = [probe.probe_id for probe in probes]
        duplicates = sorted(
            identifier for identifier in set(identifiers) if identifiers.count(identifier) > 1
        )
        if duplicates:
            raise WorkflowError(f"duplicate knowledge probe ID: {duplicates[0]}")

    @staticmethod
    def _validate_required_topics(probes: tuple[KnowledgeProbe, ...]) -> None:
        required_topics = {topic for probe in probes if (topic := probe.required_topic) is not None}
        missing = sorted(topic.value for topic in set(RequiredProbeTopic) - required_topics)
        if missing:
            raise WorkflowError(
                "knowledge probe plan is missing mandatory topics: " + ", ".join(missing)
            )
        for probe in probes:
            topic = probe.required_topic
            if topic is not None and probe.domain is not topic.domain:
                raise WorkflowError(
                    f"probe topic {topic.value} must use domain {topic.domain.value}"
                )
