from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from ..core.ledger import canonical_json
from ..core.models import WorkflowError, utc_now
from ..core.project import Project
from .contracts import (
    KnowledgeDomain,
    KnowledgeEvent,
    MaterialRedistribution,
)
from .index import KnowledgeIndex, file_sha256
from .lifecycle import ensure_knowledge_stage_running


class KnowledgeMaterialRegistrar:
    def add(
        self,
        project: Project,
        *,
        identifier: str,
        domain: KnowledgeDomain,
        path: Path,
        source_url: str,
        revision: str,
        license_note: str = "review-required",
        redistribution: MaterialRedistribution = MaterialRedistribution.UNKNOWN,
        category: str | None = None,
        authority: str | None = None,
        original: bool = True,
        index: bool = True,
        notes: str | None = None,
    ) -> dict[str, Any]:
        ensure_knowledge_stage_running(project)
        if not re.fullmatch(r"[A-Za-z0-9._-]+", identifier):
            raise WorkflowError(
                "knowledge material ID may contain only letters, digits, '.', '_' and '-'"
            )
        if not source_url.strip() or not revision.strip():
            raise WorkflowError("knowledge material source_url and revision must be non-empty")
        knowledge = KnowledgeIndex(project.root)
        controlled = path.resolve()
        if controlled != project.root and project.root not in controlled.parents:
            raise WorkflowError("knowledge material must remain inside the project workspace")
        if not controlled.is_file():
            raise WorkflowError(f"knowledge material is not a file: {controlled}")
        existing = knowledge.load_manifest()
        if any(str(record["id"]) == identifier for record in existing):
            raise WorkflowError(f"duplicate knowledge material ID: {identifier}")
        record: dict[str, Any] = {
            "id": identifier,
            "domain": domain.value,
            "path": str(controlled.relative_to(project.root)),
            "source_url": source_url,
            "revision": revision,
            "acquired_at": utc_now(),
            "license": license_note,
            "redistribution": redistribution.value,
            "sha256": file_sha256(controlled),
            "original": original,
            "index": index,
        }
        for key, value in (
            ("category", category),
            ("authority", authority),
            ("notes", notes),
        ):
            if value:
                record[key] = value
        self._write_manifest(knowledge.manifest_path, [*existing, record])
        project.record_event(
            KnowledgeEvent.MATERIAL_ADDED,
            {
                "id": identifier,
                "domain": domain.value,
                "path": record["path"],
                "sha256": record["sha256"],
            },
        )
        return record

    def add_gap(
        self,
        project: Project,
        *,
        identifier: str,
        domain: KnowledgeDomain,
        reason: str,
        revision: str,
        category: str,
    ) -> dict[str, Any]:
        ensure_knowledge_stage_running(project)
        if not reason.strip():
            raise WorkflowError("knowledge gap reason must be non-empty")
        gap_path = project.root / "knowledge" / "raw" / "gaps" / f"{identifier}.md"
        if gap_path.exists():
            raise WorkflowError(f"knowledge gap record already exists: {gap_path}")
        gap_path.parent.mkdir(parents=True, exist_ok=True)
        gap_path.write_text(
            "\n".join(
                (
                    f"# Evidence gap: {identifier}",
                    "",
                    f"Domain: {domain.value}",
                    f"Category: {category}",
                    "Status: UNKNOWN",
                    "",
                    reason.strip(),
                    "",
                )
            ),
            encoding="utf-8",
        )
        return self.add(
            project,
            identifier=identifier,
            domain=domain,
            path=gap_path,
            source_url=f"gap:{identifier}",
            revision=revision,
            license_note="not-applicable-generated-gap-record",
            redistribution=MaterialRedistribution.ALLOWED,
            category=category,
            authority="explicit-evidence-gap",
            original=False,
            notes="An explicit gap is not positive evidence and cannot authorize invention.",
        )

    @staticmethod
    def _write_manifest(path: Path, records: list[dict[str, Any]]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "".join(canonical_json(record) + "\n" for record in records),
            encoding="utf-8",
        )
