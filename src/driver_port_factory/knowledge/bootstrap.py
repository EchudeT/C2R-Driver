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
from .contracts import (
    KnowledgeArtifact,
    KnowledgeDomain,
    KnowledgeEvidenceStatus,
    KnowledgeInfrastructureError,
    KnowledgeStage,
)
from .corpus import CorpusManifest
from .index import KnowledgeIndex, file_sha256
from .lifecycle import ensure_knowledge_stage_running
from .probe_execution import KnowledgeProbeExecutor
from .probes import KnowledgeProbe, KnowledgeProbePlan
from .skill_generation import ProjectKnowledgeSkillGenerator

_INFRASTRUCTURE_GATE = "knowledge-infrastructure"


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

    def bootstrap(self, project: Project, *, probe_plan_path: Path) -> KnowledgeBootstrapResult:
        ensure_knowledge_stage_running(project)
        previous = self._previous_probe_failure(project, file_sha256(probe_plan_path.resolve()))
        if previous is not None:
            return previous
        plan = KnowledgeProbePlan.load(probe_plan_path)
        try:
            manifest = CorpusManifest.current(project)
            knowledge = KnowledgeIndex(project.root, manifest)
            probes = self._bind_originals(knowledge.manifest, plan)
            knowledge.build()
            status = knowledge.status()
        except (OSError, WorkflowError) as error:
            attempt = self._infrastructure_failure_attempt(probe_plan_path, str(error))
            project.record_artifact(
                KnowledgeStage.KNOWLEDGE_BASE,
                GeneratedArtifact(
                    KnowledgeArtifact.PROBE_ATTEMPT,
                    _json_bytes(attempt),
                    f"generated:knowledge:probe-attempt:{attempt['probe_plan_sha256']}",
                ),
            )
            raise KnowledgeInfrastructureError(str(error)) from error
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

    @classmethod
    def _previous_probe_failure(
        cls, project: Project, probe_plan_sha256: str
    ) -> KnowledgeBootstrapResult | None:
        attempts = sorted(
            (
                ref
                for ref in project.artifact_refs(stage=KnowledgeStage.KNOWLEDGE_BASE)
                if ref.kind == KnowledgeArtifact.PROBE_ATTEMPT.value
            ),
            key=lambda ref: ref.ordinal if ref.ordinal is not None else -1,
            reverse=True,
        )
        for ref in attempts:
            attempt = json.loads(project.artifacts.read(ref))
            if attempt.get("probe_plan_sha256") != probe_plan_sha256:
                continue
            failed = tuple(str(item) for item in attempt.get("failed_probe_ids", ()))
            if failed == (_INFRASTRUCTURE_GATE,):
                continue
            failed_set = set(failed)
            errors = tuple(
                cls._probe_error(result)
                for result in attempt.get("probes", ())
                if result.get("probe_id") in failed_set
            )
            return KnowledgeBootstrapResult(
                KnowledgeEvidenceStatus.FAIL,
                StageStatus.RUNNING,
                None,
                failed,
                errors,
            )
        return None

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
    def _infrastructure_failure_attempt(probe_plan_path: Path, error: str) -> dict[str, object]:
        return {
            "schema_version": 1,
            "status": KnowledgeEvidenceStatus.FAIL,
            "probe_plan_sha256": file_sha256(probe_plan_path.resolve()),
            "probes": [],
            "failed_probe_ids": [_INFRASTRUCTURE_GATE],
            "errors": [error],
            "recorded_at": utc_now(),
        }
