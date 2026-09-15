from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

from ..acquisition.contracts import AcquisitionArtifact
from ..core.contracts import ArtifactKey
from ..core.ledger import canonical_json
from ..core.models import (
    ActorRole,
    ArtifactDirection,
    ArtifactRef,
    GeneratedArtifact,
    StageStatus,
    WorkflowError,
)
from ..core.project import Project
from ..intake.contracts import IntakeArtifact
from ..migration.contracts import MigrationArtifact
from .contracts import SealingArtifact, SealingStage


@dataclass(frozen=True, slots=True)
class CandidateSeal:
    digest: str
    manifest: dict[str, object]
    artifact: ArtifactRef


class CandidateSealer:
    REQUIRED_ARTIFACTS: ClassVar[tuple[ArtifactKey, ...]] = (
        IntakeArtifact.IDENTITY_RECORD,
        AcquisitionArtifact.REVISION_MANIFEST,
        MigrationArtifact.CONTRACTS,
        MigrationArtifact.TEST_PORT_MATRIX,
        MigrationArtifact.DRIVER_SOURCE,
        MigrationArtifact.COMPLIANCE_REPORT,
        MigrationArtifact.RUNTIME_ARTIFACT,
        MigrationArtifact.ARTIFACT_IDENTITY,
        MigrationArtifact.PUBLIC_QEMU_REPORT,
        MigrationArtifact.PUBLIC_REPAIR_REPORT,
    )

    def seal(self, project: Project, *, output: Path | None = None) -> CandidateSeal:
        project.ensure_role(ActorRole.DEVELOPER, ActorRole.MIGRATION_OPERATOR)
        stage = project.stage(SealingStage.CANDIDATE_SEALING)
        if stage.status is StageStatus.READY:
            project.start(SealingStage.CANDIDATE_SEALING)
        elif stage.status is not StageStatus.RUNNING:
            raise WorkflowError(f"candidate_sealing is {stage.status.value}, not READY/RUNNING")
        refs = project.artifact_refs(direction=ArtifactDirection.OUTPUT)
        by_kind = {ref.kind for ref in refs}
        missing = sorted(
            artifact.value for artifact in self.REQUIRED_ARTIFACTS if artifact.value not in by_kind
        )
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
            "artifacts": [
                ref.to_dict()
                for ref in refs
                if ref.kind != SealingArtifact.CANDIDATE_MANIFEST.value
            ],
        }
        manifest_bytes = (canonical_json(manifest) + "\n").encode("utf-8")
        digest = hashlib.sha256(manifest_bytes).hexdigest()
        if output is not None:
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(
                json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
                encoding="utf-8",
            )
        (artifact_digest,) = project.finalize_stage(
            SealingStage.CANDIDATE_SEALING,
            (
                GeneratedArtifact(
                    SealingArtifact.CANDIDATE_MANIFEST,
                    manifest_bytes,
                    f"generated:candidate:{digest}",
                ),
            ),
        )
        ref = project.artifact(
            SealingStage.CANDIDATE_SEALING,
            SealingArtifact.CANDIDATE_MANIFEST,
        )
        if ref.digest != artifact_digest:
            raise WorkflowError("candidate manifest identity changed during finalization")
        return CandidateSeal(digest=digest, manifest=manifest, artifact=ref)
