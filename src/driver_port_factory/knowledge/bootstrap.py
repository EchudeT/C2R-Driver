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
from ..core.models import FileArtifact, GeneratedArtifact, StageStatus, WorkflowError, utc_now
from ..core.project import Project
from .cargo_dependencies import CargoDependencyClosure
from .contracts import (
    KnowledgeArtifact,
    KnowledgeDependencyClosureError,
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

_CARGO_DEPENDENCY_GATE = "target-cargo-dependency-resolution"


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
    def bootstrap(self, project: Project, *, probe_plan_path: Path) -> KnowledgeBootstrapResult:
        ensure_knowledge_stage_running(project)
        plan = KnowledgeProbePlan.load(probe_plan_path)
        manifest = CorpusManifest.current(project)
        try:
            manifest = CargoDependencyClosure().extend(project, manifest)
        except (OSError, WorkflowError) as error:
            attempt = self._dependency_failure_attempt(probe_plan_path, str(error))
            project.record_artifact(
                KnowledgeStage.KNOWLEDGE_BASE,
                GeneratedArtifact(
                    KnowledgeArtifact.PROBE_ATTEMPT,
                    _json_bytes(attempt),
                    f"generated:knowledge:probe-attempt:{attempt['probe_plan_sha256']}",
                ),
            )
            raise KnowledgeDependencyClosureError(str(error)) from error
        knowledge = KnowledgeIndex(project.root, manifest)
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
        failed_results = tuple(
            result
            for result in probe_results
            if result["required"]
            and KnowledgeEvidenceStatus(result["status"]) is not KnowledgeEvidenceStatus.PASS
        )
        failed = tuple(str(result["probe_id"]) for result in failed_results)
        errors = tuple(self._probe_error(result) for result in failed_results)
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
                KnowledgeEvidenceStatus.FAIL, StageStatus.RUNNING, None, failed, errors
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
            KnowledgeEvidenceStatus.PASS, StageStatus.PASS, str(generated_skill), (), ()
        )

    @staticmethod
    def _probe_error(result: dict[str, object]) -> str:
        probe_id = str(result["probe_id"])
        if not result["verification"]:
            return f"{probe_id}: search returned no candidate in the expected controlled original"
        verification = result["verification"]
        if not isinstance(verification, dict):
            return f"{probe_id}: search candidate could not be verified against its original"
        failures = []
        if not verification.get("locator_valid"):
            failures.append("original locator is invalid")
        if not verification.get("hash_valid"):
            failures.append("original hash does not match the controlled record")
        detail = ", ".join(failures) or "search candidate could not be verified"
        return f"{probe_id}: {detail}"

    @staticmethod
    def _bind_originals(
        manifest: CorpusManifest, plan: KnowledgeProbePlan
    ) -> tuple[KnowledgeProbe, ...]:
        return tuple(
            replace(
                probe,
                expected_record_ids=tuple(
                    sorted(
                        record.identifier
                        for record in manifest.records
                        if getattr(record.origin, "path", None) == probe.target_original
                        and record.facet.lane.value == probe.domain.value
                    )
                ),
            )
            if probe.target_original is not None
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

    @staticmethod
    def _dependency_failure_attempt(probe_plan_path: Path, error: str) -> dict[str, object]:
        return {
            "schema_version": 1,
            "status": KnowledgeEvidenceStatus.FAIL,
            "probe_plan_sha256": file_sha256(probe_plan_path.resolve()),
            "probes": [],
            "failed_probe_ids": [_CARGO_DEPENDENCY_GATE],
            "errors": [error],
            "recorded_at": utc_now(),
        }
