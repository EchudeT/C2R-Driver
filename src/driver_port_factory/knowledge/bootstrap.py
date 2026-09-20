from __future__ import annotations

import json
from dataclasses import dataclass

from ..core.models import FileArtifact, GeneratedArtifact, StageStatus, utc_now
from ..core.project import Project
from .contracts import (
    KnowledgeArtifact,
    KnowledgeEvidenceStatus,
    KnowledgeStage,
)
from .corpus import CorpusManifest
from .index import KnowledgeIndex
from .lifecycle import ensure_knowledge_stage_running
from .skill_generation import ProjectKnowledgeSkillGenerator

def _json_bytes(value: dict[str, object]) -> bytes:
    document = json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    return document.encode("utf-8")


@dataclass(frozen=True, slots=True)
class KnowledgeBootstrapResult:
    readiness: KnowledgeEvidenceStatus
    stage_status: StageStatus
    generated_skill_path: str | None
    failed_probe_ids: tuple[str, ...]
    errors: tuple[str, ...]


class KnowledgeBootstrapper:
    def build_infrastructure(self, project: Project) -> KnowledgeBootstrapResult:
        """Build the mechanical KB interface without asking Codex to fill a probe schema.

        Target-specific semantic probing belongs to the following target-study Codex stage,
        where the agent can use the generated Skill and inspect the original target tree.
        """

        ensure_knowledge_stage_running(project)
        manifest = CorpusManifest.current(project)
        knowledge = KnowledgeIndex(project.root, manifest)
        status = knowledge.build()
        generated_skill, contract = ProjectKnowledgeSkillGenerator().generate(
            project, status, knowledge.manifest
        )
        readiness = {
            "schema_version": 1,
            "status": KnowledgeEvidenceStatus.PASS,
            "index_status": status,
            "failed_probe_ids": [],
            "semantic_probe_owner": "target_platform_study",
            "recorded_at": utc_now(),
        }
        deferred_target_study = {
            "schema_version": 1,
            "status": "DEFERRED_TO_TARGET_PLATFORM_STUDY",
            "reason": (
                "Target-specific semantic probes require Codex inspection of pinned originals; "
                "the knowledge stage only builds the static index and query interface."
            ),
            "probes": [],
        }
        project.finalize_stage(
            KnowledgeStage.KNOWLEDGE_BASE,
            (
                GeneratedArtifact(
                    KnowledgeArtifact.STATUS,
                    _json_bytes(status),
                    "generated:knowledge:status",
                ),
                GeneratedArtifact(
                    KnowledgeArtifact.QUERY_CONTRACT,
                    _json_bytes(contract),
                    "generated:knowledge:query-contract",
                ),
                FileArtifact(KnowledgeArtifact.GENERATED_SKILL, generated_skill),
                GeneratedArtifact(
                    KnowledgeArtifact.READINESS_REPORT,
                    _json_bytes(readiness),
                    "generated:knowledge:readiness",
                ),
                GeneratedArtifact(
                    KnowledgeArtifact.TARGET_PROBE_RESULTS,
                    _json_bytes(deferred_target_study),
                    "generated:knowledge:target-probes-deferred",
                ),
            ),
        )
        return KnowledgeBootstrapResult(
            KnowledgeEvidenceStatus.PASS, StageStatus.PASS, str(generated_skill), (), ()
        )
