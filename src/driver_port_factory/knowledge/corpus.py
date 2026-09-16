from __future__ import annotations

import hashlib
from dataclasses import dataclass

from ..acquisition.contracts import AcquisitionArtifact, AcquisitionStage
from ..acquisition.material import MaterialRecord, parse_materials
from ..core.ledger import canonical_json
from ..core.models import StageStatus, WorkflowError
from ..core.project import Project
from ..source_analysis.contracts import SourceAnalysisArtifact, SourceAnalysisStage


@dataclass(frozen=True, slots=True)
class CorpusManifest:
    records: tuple[MaterialRecord, ...]
    data: bytes
    digest: str
    source: str

    @classmethod
    def current(cls, project: Project) -> CorpusManifest:
        if project.stage(AcquisitionStage.EVIDENCE_CLOSURE).status is not StageStatus.PASS:
            raise WorkflowError("knowledge corpus requires a passed evidence closure")
        stage = AcquisitionStage.EVIDENCE_CLOSURE
        artifact = AcquisitionArtifact.MATERIALS_MANIFEST
        if (
            SourceAnalysisStage.SOURCE_CLOSURE.value in project.workflow.stage_values
            and project.stage(SourceAnalysisStage.SOURCE_CLOSURE).status is StageStatus.PASS
        ):
            stage = SourceAnalysisStage.SOURCE_CLOSURE
            artifact = SourceAnalysisArtifact.MATERIALS_MANIFEST
        ref = project.artifact(stage, artifact)
        data = project.artifacts.read(ref)
        return cls(parse_materials(data), data, ref.digest, f"cas:sha256:{ref.digest}")

    @classmethod
    def candidate(
        cls,
        records: tuple[MaterialRecord, ...],
        *,
        parent_digest: str,
    ) -> CorpusManifest:
        data = "".join(canonical_json(record.to_dict()) + "\n" for record in records).encode()
        digest = hashlib.sha256(data).hexdigest()
        return cls(records, data, digest, f"candidate:{parent_digest}:{digest}")
