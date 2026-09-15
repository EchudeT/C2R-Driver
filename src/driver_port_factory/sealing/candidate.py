from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from ..core.models import ActorRole, ArtifactRef, StageStatus, WorkflowError
from ..core.project import Project
from ..core.store import canonical_json


@dataclass(frozen=True, slots=True)
class CandidateSeal:
    digest: str
    manifest: dict[str, object]
    artifact: ArtifactRef


class CandidateSealer:
    REQUIRED_KINDS = {
        "identity_record",
        "revision_manifest",
        "driver_source",
        "runtime_artifact",
        "artifact_identity",
        "public_qemu_report",
        "evidence_audit",
    }

    def seal(self, project: Project, *, output: Path | None = None) -> CandidateSeal:
        project.ensure_role(ActorRole.DEVELOPER, ActorRole.MIGRATION_OPERATOR)
        stage = project.store.stage("candidate_sealing")
        if stage.status is StageStatus.READY:
            project.start("candidate_sealing")
        elif stage.status is not StageStatus.RUNNING:
            raise WorkflowError(f"candidate_sealing is {stage.status.value}, not READY/RUNNING")
        refs = project.store.artifact_refs(direction="output")
        by_kind = {ref.kind for ref in refs}
        missing = sorted(self.REQUIRED_KINDS - by_kind)
        if missing:
            raise WorkflowError(
                "candidate cannot be sealed; missing public artifacts: " + ", ".join(missing)
            )
        manifest: dict[str, object] = {
            "schema_version": 1,
            "project": {
                "project_id": project.config.project_id,
                "source_platform": project.config.source_platform,
                "target_platform": project.config.target_platform,
                "driver_name": project.config.driver_name,
                "evaluation_mode": project.config.evaluation_mode.value,
            },
            "artifacts": [ref.to_dict() for ref in refs if ref.kind != "candidate_manifest"],
        }
        manifest_bytes = (canonical_json(manifest) + "\n").encode("utf-8")
        digest = hashlib.sha256(manifest_bytes).hexdigest()
        ref = project.artifacts.put_bytes(
            manifest_bytes,
            kind="candidate_manifest",
            source=f"generated:candidate:{digest}",
        )
        project.store.register_artifact(ref, stage="candidate_sealing")
        if output is not None:
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        project.complete("candidate_sealing", StageStatus.PASS)
        return CandidateSeal(digest=digest, manifest=manifest, artifact=ref)
