from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from ..core.ledger import canonical_json
from ..core.models import WorkflowError
from ..core.project import Project
from ..knowledge.contracts import KnowledgeDomain, MaterialRedistribution
from .models import CheckoutRecord, RepositoryRole


class AcquisitionMaterialCollector:
    def collect(
        self,
        project: Project,
        checkouts: tuple[CheckoutRecord, ...],
        envelope: dict[str, Any],
    ) -> list[dict[str, Any]]:
        materials = [self._repository_lock(project, record) for record in checkouts]
        source = checkout_for(checkouts, RepositoryRole.SOURCE)
        entry = (
            project.root
            / source.checkout_path
            / str(envelope["source_driver_entry_or_repository_hint"])
        )
        if entry.is_file():
            materials.append(self._source_entry(project, source, entry))
        return materials

    @staticmethod
    def _repository_lock(project: Project, record: CheckoutRecord) -> dict[str, Any]:
        lock = {
            "role": record.role.value,
            "platform": record.platform,
            "source_url": record.source_url,
            "resolved_commit": record.resolved_commit,
            "tree_id": record.tree_id,
        }
        path = project.control / "manifests" / "repository-locks" / f"{record.role.value}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(canonical_json(lock), encoding="utf-8")
        if hashlib.sha256(path.read_bytes()).hexdigest() != record.lock_sha256:
            raise WorkflowError(f"repository lock serialization changed for {record.role.value}")
        return {
            "id": f"{record.role.value}-repository-lock",
            "domain": KnowledgeDomain(record.role.value).value,
            "path": str(path.relative_to(project.root)),
            "source_url": record.source_url,
            "revision": record.resolved_commit,
            "acquired_at": record.acquired_at,
            "license": "review-required",
            "redistribution": MaterialRedistribution.UNKNOWN.value,
            "sha256": record.lock_sha256,
            "original": False,
            "index": False,
            "notes": f"repository identity for locked Git tree {record.tree_id}",
        }

    @staticmethod
    def _source_entry(
        project: Project,
        source: CheckoutRecord,
        entry: Path,
    ) -> dict[str, Any]:
        return {
            "id": "source-driver-entry",
            "domain": KnowledgeDomain.SOURCE.value,
            "path": str(entry.relative_to(project.root)),
            "source_url": source.source_url,
            "revision": source.resolved_commit,
            "acquired_at": source.acquired_at,
            "license": "review-required",
            "redistribution": MaterialRedistribution.UNKNOWN.value,
            "sha256": hashlib.sha256(entry.read_bytes()).hexdigest(),
            "original": True,
            "notes": "frozen driver entry; the recursive source closure is produced later",
        }


def checkout_for(records: tuple[CheckoutRecord, ...], role: RepositoryRole) -> CheckoutRecord:
    matches = [record for record in records if record.role is role]
    if len(matches) != 1:
        raise WorkflowError(f"acquisition result requires exactly one {role.value} checkout")
    return matches[0]
