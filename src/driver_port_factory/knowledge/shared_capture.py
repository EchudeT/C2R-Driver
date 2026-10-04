"""Explicit operator export of controlled originals and archived public execution observations."""

import json

from ..core.models import ActorRole, EvaluationMode, WorkflowError
from ..migration.contracts import MigrationArtifact, MigrationStage
from .corpus import CorpusManifest
from .index import KnowledgeIndex
from .shared_store import digest


def capture(library, project):
    if (
        project.config.evaluation_mode is not EvaluationMode.DEVELOPER_EVIDENCE
        or project.config.actor_role is not ActorRole.DEVELOPER
    ):
        raise WorkflowError("Shared learning export only accepts developer public evidence")
    from ..acquisition.repository import load_repository_acquisition
    from ..acquisition.repository_role import RepositoryRole

    project.verify_integrity()
    target = next(
        r for r in load_repository_acquisition(project).checkouts if r.role is RepositoryRole.TARGET
    )
    index = KnowledgeIndex.for_project(project)
    index.status()
    entries = []
    with library.writing():
        for record in CorpusManifest.current(project).records:
            data = index.controlled_path(record.path).read_bytes()
            if digest(data) != record.sha256:
                raise WorkflowError("Original changed during shared export")
            domain = record.facet.lane.value
            platform = (
                project.config.target_platform
                if domain == "target"
                else project.config.source_platform
                if domain in {"source", "test"}
                else domain
            )
            entries.append(
                {
                    "schema_version": 1,
                    "kind": "ORIGINAL",
                    "domain": domain,
                    "platform": platform,
                    "revision": record.revision,
                    "title": record.path,
                    "blob": library.put(data),
                    "source_url": record.source_url,
                    "license": record.license_note,
                    "origin": record.to_dict(),
                    "authority": "CONTROLLED_TASK_ORIGINAL",
                }
            )
        # Includes failed attempts, not only the final PASS. No private assertions or reviewer text.
        for ref in project.artifact_refs(stage=MigrationStage.PUBLIC_QEMU_VALIDATION):
            if ref.kind not in {
                MigrationArtifact.PUBLIC_QEMU_REPORT.value,
                MigrationArtifact.PUBLIC_QEMU_ATTEMPT.value,
            }:
                continue
            data = project.artifacts.read(ref)
            receipt = json.loads(data)
            run = receipt.get("run", receipt)
            attachments = []
            for log in run.get("logs", []):
                archive = log.get("archive_path")
                if not archive:
                    raise WorkflowError("Public observation has no immutable archived log")
                from pathlib import Path

                path = Path(archive).resolve()
                if project.root.resolve() not in path.parents:
                    raise WorkflowError("Public observation log is outside its workspace")
                raw = path.read_bytes()
                if digest(raw) != log["sha256"]:
                    raise WorkflowError("Public observation log has changed")
                attachments.append({"blob": library.put(raw), "source": str(path)})
            entries.append(
                {
                    "schema_version": 1,
                    "kind": "OBSERVATION",
                    "domain": "target",
                    "platform": project.config.target_platform,
                    "revision": target.resolved_commit,
                    "title": f"{project.config.driver_name} public {ref.kind}",
                    "driver": project.config.driver_name,
                    "project": project.config.project_id,
                    "blob": library.put(data),
                    "receipt_sha256": ref.digest,
                    "attachments": attachments,
                    "status": "ARCHIVED_PUBLIC_EXECUTION_NOT_GENERAL_SEMANTIC_PROOF",
                }
            )
        return library.commit(entries)
