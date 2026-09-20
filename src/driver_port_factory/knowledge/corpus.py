from __future__ import annotations

import hashlib
from dataclasses import dataclass

from ..acquisition.contracts import AcquisitionArtifact, AcquisitionStage
from ..acquisition.material import MaterialRecord, parse_materials
from ..core.ledger import canonical_json
from ..core.models import StageStatus, WorkflowError
from ..core.project import Project
from ..source_analysis.contracts import SourceAnalysisArtifact, SourceAnalysisStage
from .contracts import KnowledgeArtifact, KnowledgeStage


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
        from ..source_analysis.preparation import latest, artifact as prepared_artifact
        if (project.stage(SourceAnalysisStage.SOURCE_CLOSURE).status is StageStatus.RUNNING
                and latest(project)):
            ref = prepared_artifact(project, SourceAnalysisArtifact.MATERIALS_MANIFEST)
            data = project.artifacts.read(ref)
            return cls(parse_materials(data), data, ref.digest, f"cas:sha256:{ref.digest}")
        if (
            SourceAnalysisStage.SOURCE_CLOSURE.value in project.workflow.stage_values
            and project.stage(SourceAnalysisStage.SOURCE_CLOSURE).status is StageStatus.PASS
        ):
            stage = SourceAnalysisStage.SOURCE_CLOSURE
            artifact = SourceAnalysisArtifact.MATERIALS_MANIFEST
        elif project.stage(KnowledgeStage.KNOWLEDGE_BASE).status is StageStatus.PASS:
            status = project.load_json_artifact(
                KnowledgeStage.KNOWLEDGE_BASE, KnowledgeArtifact.STATUS
            )
            path = (project.root / str(status["manifest_path"])).resolve()
            if project.root not in path.parents or not path.is_file():
                raise WorkflowError("knowledge corpus manifest is unavailable")
            data = path.read_bytes()
            digest = hashlib.sha256(data).hexdigest()
            if digest != status["manifest_sha256"]:
                raise WorkflowError("knowledge corpus manifest differs from readiness status")
            return cls(parse_materials(data), data, digest, str(path))
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
