from __future__ import annotations

import json
from dataclasses import dataclass, replace
from pathlib import Path

from ..acquisition.contracts import AcquisitionArtifact, AcquisitionStage
from ..acquisition.facets import (
    EvidenceFacet,
    EvidenceLane,
    MaterialRedistribution,
    TargetFacet,
)
from ..acquisition.git_material_retrieval import GitMaterialRetriever
from ..acquisition.locators import GitBlobLocator, MaterialPolicy
from ..acquisition.repository import load_repository_acquisition
from ..acquisition.repository_role import RepositoryRole
from ..acquisition.retrieval import material_identifier
from ..core.models import FileArtifact, GeneratedArtifact, StageStatus, utc_now
from ..core.project import Project
from .contracts import (
    KnowledgeArtifact,
    KnowledgeDomain,
    KnowledgeEvidenceStatus,
    KnowledgeStage,
)
from .corpus import CorpusManifest
from .index import KnowledgeIndex, file_sha256
from .lifecycle import ensure_knowledge_stage_running
from .probe_execution import KnowledgeProbeExecutor
from .probes import KnowledgeProbe, KnowledgeProbePlan
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


class KnowledgeBootstrapper:
    def bootstrap(self, project: Project, *, probe_plan_path: Path) -> KnowledgeBootstrapResult:
        ensure_knowledge_stage_running(project)
        knowledge = KnowledgeIndex.for_project(project)
        plan = KnowledgeProbePlan.load(probe_plan_path)
        probes = self._bind_originals(knowledge.manifest, plan)
        knowledge.build()
        status = knowledge.status()
        executor = KnowledgeProbeExecutor()
        gap_register = project.load_json_artifact(
            AcquisitionStage.EVIDENCE_CLOSURE,
            AcquisitionArtifact.EVIDENCE_GAP_REGISTER,
        )
        gaps = tuple(gap_register["gaps"])
        probe_results = [executor.run(knowledge, probe, gaps) for probe in probes]
        repair = tuple(
            probe
            for probe, result in zip(probes, probe_results, strict=True)
            if result["status"] == KnowledgeEvidenceStatus.FAIL
            and probe.target_original is not None
            and not probe.expected_record_ids
        )
        if repair:
            knowledge = self._repair_target_corpus(project, knowledge, repair)
            probes = self._bind_originals(knowledge.manifest, plan)
            knowledge.build()
            status = knowledge.status()
            probe_results = [executor.run(knowledge, probe, gaps) for probe in probes]
        failed = tuple(
            str(result["probe_id"])
            for result in probe_results
            if result["required"]
            and KnowledgeEvidenceStatus(result["status"]) is not KnowledgeEvidenceStatus.PASS
        )
        attempt = self._attempt(probe_plan_path, status, probe_results, failed)
        if failed:
            project.record_artifact(
                KnowledgeStage.KNOWLEDGE_BASE,
                GeneratedArtifact(
                    KnowledgeArtifact.PROBE_ATTEMPT,
                    _json_bytes(attempt),
                    f"generated:knowledge:probe-attempt:{attempt['probe_plan_sha256']}",
                ),
            )
            return KnowledgeBootstrapResult(
                KnowledgeEvidenceStatus.FAIL, StageStatus.RUNNING, None, failed
            )

        generated_skill, contract = ProjectKnowledgeSkillGenerator().generate(
            project, status, knowledge.manifest
        )
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
                    _json_bytes(attempt),
                    "generated:knowledge:readiness",
                ),
                GeneratedArtifact(
                    KnowledgeArtifact.TARGET_PROBE_RESULTS,
                    _json_bytes(
                        {
                            "schema_version": 1,
                            "status": KnowledgeEvidenceStatus.PASS,
                            "probes": [
                                result
                                for result in probe_results
                                if result["domain"] == KnowledgeDomain.TARGET.value
                            ],
                        }
                    ),
                    "generated:knowledge:target-probes",
                ),
            ),
        )
        return KnowledgeBootstrapResult(
            KnowledgeEvidenceStatus.PASS, StageStatus.PASS, str(generated_skill), ()
        )

    @staticmethod
    def _bind_originals(
        manifest: CorpusManifest, plan: KnowledgeProbePlan
    ) -> tuple[KnowledgeProbe, ...]:
        target_records = {
            record.origin.path: record.identifier
            for record in manifest.records
            if getattr(record.origin, "repository", None) is RepositoryRole.TARGET
        }
        return tuple(
            replace(probe, expected_record_ids=(target_records[probe.target_original],))
            if probe.target_original is not None and probe.target_original in target_records
            else probe
            for probe in plan.probes
        )

    @staticmethod
    def _repair_target_corpus(
        project: Project, knowledge: KnowledgeIndex, probes: tuple[KnowledgeProbe, ...]
    ) -> KnowledgeIndex:
        acquisition = load_repository_acquisition(project)
        target = acquisition.checkout(RepositoryRole.TARGET)
        retriever = GitMaterialRetriever(project.root, acquisition)
        additions = []
        added_paths: set[str] = set()
        for probe in probes:
            topic = probe.required_topic
            if topic is None or probe.target_original is None:
                continue
            if probe.target_original in added_paths:
                continue
            added_paths.add(probe.target_original)
            facet = EvidenceFacet(EvidenceLane.TARGET, TargetFacet.API_DEFINITIONS_AND_CALLS)
            locator = GitBlobLocator(
                RepositoryRole.TARGET,
                probe.target_original,
                MaterialPolicy("review-required", MaterialRedistribution.UNKNOWN, True),
            )
            record = retriever.retrieve(facet, locator, material_identifier(facet, locator)).record
            additions.append(replace(record, acquired_at=target.acquired_at))
        corpus = CorpusManifest.candidate(
            (*knowledge.manifest.records, *additions),
            parent_digest=knowledge.manifest.digest,
        )
        return KnowledgeIndex(project.root, corpus)

    @staticmethod
    def _attempt(
        probe_plan_path: Path,
        status: dict[str, object],
        probe_results: list[dict[str, object]],
        failed: tuple[str, ...],
    ) -> dict[str, object]:
        return {
            "schema_version": 1,
            "status": (
                KnowledgeEvidenceStatus.PASS if not failed else KnowledgeEvidenceStatus.FAIL
            ),
            "probe_plan_sha256": file_sha256(probe_plan_path.resolve()),
            "index_status": status,
            "probes": probe_results,
            "failed_probe_ids": list(failed),
            "recorded_at": utc_now(),
        }
