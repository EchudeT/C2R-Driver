from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..acquisition.material import MaterialRecord
from ..acquisition.parsing import sha256
from ..core.models import WorkflowError
from ..knowledge.contracts import KnowledgeIndexStatus


@dataclass(frozen=True, slots=True)
class SourceCorpusRevision:
    parent_manifest_sha256: str
    added_materials: tuple[MaterialRecord, ...]
    manifest_sha256: str
    index_status: dict[str, Any]

    @classmethod
    def from_dict(cls, value: object) -> SourceCorpusRevision:
        if not isinstance(value, dict) or value.get("schema_version") != 1:
            raise WorkflowError("knowledge_revision must be a schema_version=1 object")
        parent = sha256(
            value.get("parent_manifest_sha256"),
            "knowledge revision parent manifest",
        )
        successor = sha256(
            value.get("manifest_sha256"),
            "knowledge revision successor manifest",
        )
        additions = value.get("added_materials")
        if not isinstance(additions, list):
            raise WorkflowError("knowledge_revision.added_materials must be a list")
        index_status = value.get("index_status")
        if not isinstance(index_status, dict):
            raise WorkflowError("knowledge_revision requires an index status")
        try:
            status = KnowledgeIndexStatus(index_status.get("status"))
        except (TypeError, ValueError) as error:
            raise WorkflowError("knowledge_revision has an invalid index status") from error
        if status is not KnowledgeIndexStatus.READY:
            raise WorkflowError("knowledge_revision requires a rebuilt READY index")
        if index_status.get("manifest_sha256") != successor:
            raise WorkflowError("knowledge_revision index does not bind the successor manifest")
        return cls(
            parent,
            tuple(MaterialRecord.from_dict(item) for item in additions),
            successor,
            dict(index_status),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "reason": "validated source closure expanded the controlled corpus",
            "parent_manifest_sha256": self.parent_manifest_sha256,
            "added_materials": [record.to_dict() for record in self.added_materials],
            "manifest_sha256": self.manifest_sha256,
            "index_status": self.index_status,
        }
