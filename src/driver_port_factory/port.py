from __future__ import annotations

import argparse
import json
import os
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .acquisition.closure import EvidenceClosureFinalizer
from .acquisition.contracts import AcquisitionArtifact, AcquisitionStage
from .acquisition.job import ArtifactOccurrence
from .acquisition.proposal import EvidenceProposalImporter
from .acquisition.repository import RepositoryAcquirer, load_repository_acquisition
from .acquisition.repository_role import RepositoryRole
from .acquisition.revision_proposal import RevisionProposalImporter
from .acquisition.revision_selection import RevisionSelector
from .cli_support import CommandRegistry, command_registry
from .codex.cli import run_codex_stage
from .codex.contracts import CodexArtifact, CodexBackend, CodexExecEventType
from .composition import initialize_project, open_project
from .core.contracts import ArtifactKey, StageKey
from .core.models import (
    ActorRole,
    ArtifactDirection,
    EvaluationMode,
    ProjectConfig,
    StageStatus,
    WorkflowError,
)
from .core.project import Project
from .environment.contracts import EnvironmentArtifact, EnvironmentStage
from .environment.execution import ExperimentExecutor
from .environment.inventory import EnvironmentInspector
from .environment.models import ExperimentReadiness
from .environment.planning import ExperimentPlanRegistrar
from .intake.contracts import IntakeArtifact, IntakeStage
from .intake.service import IntakeService
from .knowledge.bootstrap import KnowledgeBootstrapper
from .knowledge.contracts import KnowledgeArtifact, KnowledgeEvidenceStatus, KnowledgeStage
from .migration.artifact_preparation import ArtifactPreparationPlan, ArtifactPreparationService
from .migration.cli import (
    DRIVER_IMPLEMENTATION_OBJECTIVE,
    MIGRATION_CONTRACTS_OBJECTIVE,
    PUBLIC_QEMU_OBJECTIVE,
    PUBLIC_REPAIR_OBJECTIVE,
    TARGET_COMPLIANCE_AND_ARTIFACT_OBJECTIVE,
    TEST_ADAPTATION_OBJECTIVE,
)
from .migration.completion_audit import CompletionAuditService
from .migration.compliance import ComplianceReport, ComplianceService
from .migration.contract_set import MigrationContractService, MigrationContractSet
from .migration.contracts import MigrationArtifact, MigrationStage
from .migration.handoff import MigrationHandoff
from .migration.implementation import DriverImplementationService, ImplementationResponse
from .migration.public_qemu import PublicQemuPlan, PublicQemuService
from .migration.public_repair import PublicRepairPlan, PublicRepairService
from .migration.test_matrix import TestSelectionMatrix, TestSelectionService
from .source_analysis.clang_backend import AnalyzerFamily
from .source_analysis.closure import SourceClosureService
from .source_analysis.contracts import SourceAnalysisArtifact, SourceAnalysisStage
from .source_analysis.structured import StructuredCAnalysisService
from .target_study.contracts import TargetStudyArtifact, TargetStudyStage
from .target_study.service import TargetStudyService

REVISION_OBJECTIVE = (
    "Select exact maintained source, target, and QEMU revisions for the frozen driver scope. "
    "Every evidence excerpt must be one byte-for-byte contiguous substring of the retrieved "
    "source; never join fragments or use ellipses. "
    "Include at least one cross-repository compatibility citation whose bindings contain all "
    "three selected source, target, and QEMU refs. "
    "Return only the revision-selection proposal required by the output schema."
)
EVIDENCE_OBJECTIVE = (
    "Propose the minimum complete evidence closure for the frozen driver and repositories. "
    "Include every one of the 25 required evidence facets exactly once. "
    "Return only the evidence-closure proposal required by the output schema."
)
ENVIRONMENT_OBJECTIVE = (
    "Select one concrete, least-cost QEMU/QMP experiment route from the frozen repositories and "
    "host inventory. Return only one schema_version=2 environment experiment plan."
)
KNOWLEDGE_OBJECTIVE = (
    "Create the mandatory target-specific knowledge probe plan from controlled originals. "
    "Return only one schema_version=1 object containing the complete probes array."
)
TARGET_STUDY_OBJECTIVE = (
    "Complete the target-platform study from pinned originals. Return only one JSON object with "
    "profile_json, profile_markdown, api_table, analogous_trace, and change_plan."
)
SOURCE_CLOSURE_OBJECTIVE = (
    "Close the behaviorally required source set using the frozen compile commands. Return only "
    "one source-closure JSON object for the controller to verify."
)
CODEX_GATE_CORRECTION_ATTEMPTS = 3


@dataclass(frozen=True, slots=True)
class PortOptions:
    workspace: Path
    source_platform: str
    target_platform: str
    driver_name: str
    skill_root: Path
    catalogs: tuple[Path, ...]
    backend: CodexBackend
    codex_bin: str
    model: str | None
    analyzer: str
    analyzer_family: AnalyzerFamily


@dataclass(frozen=True, slots=True)
class PortOutcome:
    stage: str
    status: StageStatus
    next_action: str
    audit_path: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "stage": self.stage,
            "status": self.status.value,
            "next_action": self.next_action,
            "audit_path": self.audit_path,
        }


class PortRunner:
    """Resume and execute the sole developer workflow up to its completion audit."""

    def __init__(self, options: PortOptions) -> None:
        self.options = options
        self._actions: dict[StageKey, Callable[[Project], None]] = {
            IntakeStage.REQUEST: self._intake,
            AcquisitionStage.REVISION_SELECTION: self._revisions,
            AcquisitionStage.REPOSITORY_ACQUISITION: self._repositories,
            AcquisitionStage.EVIDENCE_CLOSURE: self._evidence,
            EnvironmentStage.RECOVERY: self._environment,
            KnowledgeStage.KNOWLEDGE_BASE: self._knowledge,
            TargetStudyStage.STUDY: self._target_study,
            MigrationStage.HANDOFF: self._handoff,
            SourceAnalysisStage.SOURCE_CLOSURE: self._source_closure,
            SourceAnalysisStage.STRUCTURED_C_ANALYSIS: self._structured_c,
            MigrationStage.CONTRACTS: self._contracts,
            MigrationStage.TEST_ADAPTATION: self._test_adaptation,
            MigrationStage.DRIVER_IMPLEMENTATION: self._implementation,
            MigrationStage.TARGET_COMPLIANCE: self._compliance,
            MigrationStage.ARTIFACT_PREPARATION: self._artifact_preparation,
            MigrationStage.PUBLIC_QEMU_VALIDATION: self._public_qemu,
            MigrationStage.PUBLIC_REPAIR: self._public_repair,
            MigrationStage.COMPLETION_AUDIT: self._completion_audit,
        }

    def run(self) -> PortOutcome:
        project = self._project()
        while True:
            stage = self._current(project)
            if stage is None:
                audit = project.artifact(
                    MigrationStage.COMPLETION_AUDIT, MigrationArtifact.EVIDENCE_AUDIT
                )
                return PortOutcome(
                    MigrationStage.COMPLETION_AUDIT.value,
                    StageStatus.PASS,
                    "complete",
                    str(project.artifacts.path_for_digest(audit.digest)),
                )
            if stage.status is StageStatus.WAITING_FOR_USER:
                return PortOutcome(
                    stage.name.value, stage.status, "answer persisted intake question"
                )
            action = self._actions.get(stage.name)
            if action is None or stage.status not in {StageStatus.READY, StageStatus.RUNNING}:
                return PortOutcome(stage.name.value, stage.status, "resolve recorded blocker")
            action(project)
            current = project.stage(stage.name)
            if current.status is StageStatus.WAITING_FOR_USER:
                return PortOutcome(
                    current.name.value, current.status, "answer persisted intake question"
                )
            if current.status is StageStatus.RUNNING:
                return PortOutcome(
                    current.name.value, current.status, "rerun to continue bounded repair"
                )

    def _project(self) -> Project:
        workspace = self.options.workspace.resolve()
        self._ensure_git_root(workspace)
        control = workspace / Project.CONTROL_DIR
        if control.exists():
            project = open_project(workspace)
            expected = (
                self.options.source_platform,
                self.options.target_platform,
                self.options.driver_name,
            )
            actual = (
                project.config.source_platform,
                project.config.target_platform,
                project.config.driver_name,
            )
            if actual != expected:
                raise WorkflowError("run inputs differ from the persisted migration request")
            return project
        return initialize_project(
            workspace,
            ProjectConfig(
                project_id=workspace.name,
                source_platform=self.options.source_platform,
                target_platform=self.options.target_platform,
                driver_name=self.options.driver_name,
                evaluation_mode=EvaluationMode.DEVELOPER_EVIDENCE,
                actor_role=ActorRole.DEVELOPER,
                skill_root=str(self.options.skill_root.resolve()),
            ),
        )

    @staticmethod
    def _ensure_git_root(workspace: Path) -> None:
        workspace.mkdir(parents=True, exist_ok=True)
        if (workspace / ".git").exists():
            return
        completed = subprocess.run(
            ["git", "init", "--quiet", "--initial-branch=main", str(workspace)],
            text=True,
            capture_output=True,
            check=False,
        )
        if completed.returncode != 0:
            raise WorkflowError(
                f"cannot initialize local Git workspace: {completed.stderr.strip()}"
            )

    @staticmethod
    def _current(project: Project):
        unfinished = [
            stage
            for stage in project.stages()
            if stage.status not in {StageStatus.PASS, StageStatus.NOT_APPLICABLE}
        ]
        if not unfinished:
            return None
        actionable = [
            stage
            for stage in unfinished
            if stage.status in {StageStatus.READY, StageStatus.RUNNING}
        ]
        waiting = [stage for stage in unfinished if stage.status is StageStatus.WAITING_FOR_USER]
        return min(actionable or waiting or unfinished, key=lambda stage: stage.position)

    def _codex(
        self,
        project: Project,
        stage: StageKey,
        objective: str,
        context: dict[str, object],
        *,
        thread_id: str | None = None,
        follow_up: str | None = None,
    ):
        return run_codex_stage(
            project,
            stage,
            objective=objective,
            context=context,
            backend=self.options.backend,
            codex_bin=self.options.codex_bin,
            model=self.options.model,
            thread_id=thread_id,
            follow_up=follow_up,
        )

    @staticmethod
    def _artifact_context(
        project: Project,
        stage: StageKey,
        kind: ArtifactKey,
        direction: ArtifactDirection = ArtifactDirection.OUTPUT,
    ) -> dict[str, object]:
        reference = project.artifact(stage, kind, direction=direction)
        return {
            **reference.to_dict(),
            "path": str(project.artifacts.path_for_digest(reference.digest)),
        }

    @staticmethod
    def _job_occurrence(
        project: Project, stage: StageKey, response_path: Path
    ) -> ArtifactOccurrence:
        matches = [
            ref
            for ref in project.artifact_refs(stage=stage, direction=ArtifactDirection.OUTPUT)
            if ref.kind == CodexArtifact.JOB_RESULT.value and ref.source == str(response_path)
        ]
        if len(matches) != 1 or matches[0].ordinal is None:
            raise WorkflowError("Codex result occurrence was not persisted uniquely")
        return ArtifactOccurrence(matches[0].digest, matches[0].ordinal)

    @staticmethod
    def _latest_job_occurrence(project: Project, stage: StageKey) -> ArtifactOccurrence | None:
        matches = [
            ref
            for ref in project.artifact_refs(stage=stage, direction=ArtifactDirection.OUTPUT)
            if ref.kind == CodexArtifact.JOB_RESULT.value and ref.ordinal is not None
        ]
        if not matches:
            return None
        latest = max(matches, key=lambda ref: ref.ordinal)
        return ArtifactOccurrence(latest.digest, latest.ordinal)

    @staticmethod
    def _latest_thread_id(project: Project, stage: StageKey) -> str | None:
        logs = [
            ref
            for ref in project.artifact_refs(stage=stage, direction=ArtifactDirection.OUTPUT)
            if ref.kind == CodexArtifact.EVENT_LOG.value and ref.ordinal is not None
        ]
        if not logs:
            return None
        latest = max(logs, key=lambda ref: ref.ordinal)
        for line in project.artifacts.read(latest).decode("utf-8").splitlines():
            event = json.loads(line)
            if event.get("type") == CodexExecEventType.THREAD_STARTED.value:
                thread_id = event.get("thread_id")
                return thread_id if isinstance(thread_id, str) else None
        return None

    def _codex_gate(
        self,
        project: Project,
        stage: StageKey,
        objective: str,
        context: dict[str, object],
        accept: Callable[[Project, ArtifactOccurrence], None],
    ) -> None:
        thread_id = None
        follow_up = None
        last_error: WorkflowError | None = None
        pending = self._latest_job_occurrence(project, stage)
        if pending is not None:
            try:
                accept(project, pending)
                return
            except WorkflowError as error:
                last_error = error
                thread_id = self._latest_thread_id(project, stage)
                follow_up = self._codex_correction(error)
        for _ in range(CODEX_GATE_CORRECTION_ATTEMPTS):
            result, _, response = self._codex(
                project,
                stage,
                objective,
                context,
                thread_id=thread_id,
                follow_up=follow_up,
            )
            try:
                accept(project, self._job_occurrence(project, stage, response))
                return
            except WorkflowError as error:
                last_error = error
                if not result.thread_id:
                    raise
                thread_id = result.thread_id
                follow_up = self._codex_correction(error)
        raise WorkflowError(f"{stage.value} failed after same-session corrections: {last_error}")

    @staticmethod
    def _codex_correction(error: WorkflowError) -> str:
        return (
            "The controller rejected the previous proposal: "
            f"{error}. Preserve all proposal requirements and previously accepted evidence, "
            "correct the failure, re-check every cited or located input, and return only a "
            "complete replacement matching the same output schema."
        )

    @staticmethod
    def _write_response_parts(
        project: Project,
        stage: StageKey,
        job: ArtifactOccurrence,
        names: tuple[str, ...],
    ) -> dict[str, Path]:
        matches = [
            ref
            for ref in project.artifact_refs(stage=stage, direction=ArtifactDirection.OUTPUT)
            if ref.kind == CodexArtifact.JOB_RESULT.value
            and ref.digest == job.digest
            and ref.ordinal == job.ordinal
        ]
        if len(matches) != 1:
            raise WorkflowError("Codex result occurrence was not persisted uniquely")
        path = project.artifacts.path_for_digest(job.digest)
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise WorkflowError("Codex stage response must be UTF-8 JSON") from error
        if not isinstance(value, dict):
            raise WorkflowError("Codex stage response must be a JSON object")
        if set(value) != set(names):
            raise WorkflowError(f"Codex stage response has the wrong {stage.value} documents")
        output = project.control / "generated" / stage.value / Path(matches[0].source).stem
        parts: dict[str, Path] = {}
        for name in names:
            document = value[name]
            suffix = ".md" if isinstance(document, str) else ".json"
            target = output / f"{name}{suffix}"
            data = (
                document if isinstance(document, str) else json.dumps(document, indent=2) + "\n"
            ).encode("utf-8")
            if target.exists() and target.read_bytes() != data:
                raise WorkflowError("materialized Codex response differs from immutable result")
            parts[name] = target
        output.mkdir(parents=True, exist_ok=True)
        for name, target in parts.items():
            if not target.exists():
                document = value[name]
                target.write_text(
                    document
                    if isinstance(document, str)
                    else json.dumps(document, indent=2) + "\n",
                    encoding="utf-8",
                )
        return parts

    def _intake(self, project: Project) -> None:
        IntakeService().analyze(
            project,
            raw_request=(
                f"Port {self.options.driver_name} from {self.options.source_platform} "
                f"to {self.options.target_platform} in Rust and validate it with QEMU."
            ),
            catalog_paths=self.options.catalogs,
        )

    def _revisions(self, project: Project) -> None:
        envelope = self._artifact_context(
            project,
            IntakeStage.ENVELOPE_FREEZE,
            IntakeArtifact.MIGRATION_ENVELOPE,
        )
        self._codex_gate(
            project,
            AcquisitionStage.REVISION_SELECTION,
            REVISION_OBJECTIVE,
            {"migration_envelope": envelope},
            self._accept_revision_result,
        )

    @staticmethod
    def _accept_revision_result(project: Project, job: ArtifactOccurrence) -> None:
        proposal = RevisionProposalImporter().import_job_result(
            project, job_digest=job.digest, job_ordinal=job.ordinal
        )
        RevisionSelector().select(project, proposal=proposal)

    @staticmethod
    def _repositories(project: Project) -> None:
        RepositoryAcquirer().acquire(project)

    def _evidence(self, project: Project) -> None:
        inputs = {
            kind.value: self._artifact_context(project, stage, kind)
            for stage, kind in (
                (
                    IntakeStage.ENVELOPE_FREEZE,
                    IntakeArtifact.MIGRATION_ENVELOPE,
                ),
                (AcquisitionStage.REPOSITORY_ACQUISITION, AcquisitionArtifact.REPOSITORY_MANIFEST),
            )
        }
        self._codex_gate(
            project,
            AcquisitionStage.EVIDENCE_CLOSURE,
            EVIDENCE_OBJECTIVE,
            inputs,
            self._accept_evidence_result,
        )

    @staticmethod
    def _accept_evidence_result(project: Project, job: ArtifactOccurrence) -> None:
        imported = EvidenceProposalImporter().import_job_result(
            project, job_digest=job.digest, job_ordinal=job.ordinal
        )
        EvidenceClosureFinalizer().finalize(project, proposal=imported.occurrence)

    def _environment(self, project: Project) -> None:
        if project.stage(EnvironmentStage.RECOVERY).status is StageStatus.READY:
            EnvironmentInspector().inspect(project)
        self._codex_gate(
            project,
            EnvironmentStage.RECOVERY,
            ENVIRONMENT_OBJECTIVE,
            {
                kind.value: self._artifact_context(
                    project,
                    EnvironmentStage.RECOVERY,
                    kind,
                    ArtifactDirection.INPUT,
                )
                for kind in (EnvironmentArtifact.INVENTORY, EnvironmentArtifact.MODE_CANDIDATES)
            },
            self._accept_environment_result,
        )

    @staticmethod
    def _accept_environment_result(project: Project, job: ArtifactOccurrence) -> None:
        response = project.artifacts.path_for_digest(job.digest)
        plan = ExperimentPlanRegistrar().register(project, response)
        result = ExperimentExecutor().run(project, plan.route_id)
        if result.readiness is ExperimentReadiness.FAIL:
            raise WorkflowError(result.message)

    def _knowledge(self, project: Project) -> None:
        self._codex_gate(
            project,
            KnowledgeStage.KNOWLEDGE_BASE,
            KNOWLEDGE_OBJECTIVE,
            {
                kind.value: self._artifact_context(project, AcquisitionStage.EVIDENCE_CLOSURE, kind)
                for kind in (
                    AcquisitionArtifact.MATERIALS_MANIFEST,
                    AcquisitionArtifact.EVIDENCE_GAP_REGISTER,
                )
            },
            self._accept_knowledge_result,
        )

    @staticmethod
    def _accept_knowledge_result(project: Project, job: ArtifactOccurrence) -> None:
        result = KnowledgeBootstrapper().bootstrap(
            project, probe_plan_path=project.artifacts.path_for_digest(job.digest)
        )
        if result.readiness is KnowledgeEvidenceStatus.FAIL:
            raise WorkflowError("knowledge probes failed: " + "; ".join(result.errors))

    def _target_study(self, project: Project) -> None:
        acquisition = load_repository_acquisition(project)
        target = acquisition.checkout(RepositoryRole.TARGET)
        self._codex_gate(
            project,
            TargetStudyStage.STUDY,
            TARGET_STUDY_OBJECTIVE,
            {
                "target_repository": {
                    "path": str((project.root / target.checkout_path).resolve()),
                    "revision": target.resolved_commit,
                },
                "knowledge": self._artifact_context(
                    project, KnowledgeStage.KNOWLEDGE_BASE, KnowledgeArtifact.QUERY_CONTRACT
                ),
            },
            self._accept_target_study_result,
        )

    def _accept_target_study_result(self, project: Project, job: ArtifactOccurrence) -> None:
        parts = self._write_response_parts(
            project,
            TargetStudyStage.STUDY,
            job,
            (
                "profile_json",
                "profile_markdown",
                "api_table",
                "analogous_trace",
                "change_plan",
            ),
        )
        result = TargetStudyService().validate(
            project,
            profile_json=parts["profile_json"],
            profile_markdown=parts["profile_markdown"],
            api_table=parts["api_table"],
            analogous_trace=parts["analogous_trace"],
            change_plan=parts["change_plan"],
        )
        if result.errors:
            raise WorkflowError("target study failed: " + "; ".join(result.errors))

    @staticmethod
    def _handoff(project: Project) -> None:
        MigrationHandoff().create(project)

    def _source_closure(self, project: Project) -> None:
        handoff = project.load_json_artifact(MigrationStage.HANDOFF, MigrationArtifact.HANDOFF)
        source = load_repository_acquisition(project).checkout(RepositoryRole.SOURCE)
        self._codex_gate(
            project,
            SourceAnalysisStage.SOURCE_CLOSURE,
            SOURCE_CLOSURE_OBJECTIVE,
            {
                "migration_handoff": self._artifact_context(
                    project, MigrationStage.HANDOFF, MigrationArtifact.HANDOFF
                ),
                "driver_identity": handoff["identity"],
                "source_repository": {
                    "root": str((project.root / source.checkout_path).resolve()),
                    "revision": source.resolved_commit,
                    "read_only": True,
                },
                "source_paths": handoff["evidence"]["source_paths"],
                "source_test_paths": handoff["evidence"]["source_test_paths"],
                "known_gaps": handoff["evidence"]["known_gaps"],
                "knowledge": handoff["knowledge"],
            },
            self._accept_source_closure_result,
        )

    @staticmethod
    def _accept_source_closure_result(project: Project, job: ArtifactOccurrence) -> None:
        result = SourceClosureService().validate(
            project, closure_path=project.artifacts.path_for_digest(job.digest)
        )
        if result.errors:
            raise WorkflowError("source closure failed: " + "; ".join(result.errors))

    def _structured_c(self, project: Project) -> None:
        StructuredCAnalysisService().analyze(
            project,
            analyzer=self.options.analyzer,
            analyzer_family=self.options.analyzer_family,
        )

    def _contracts(self, project: Project) -> None:
        self._codex_gate(
            project,
            MigrationStage.CONTRACTS,
            MIGRATION_CONTRACTS_OBJECTIVE,
            self._migration_context(
                project,
                (
                    (MigrationStage.HANDOFF, MigrationArtifact.HANDOFF),
                    (KnowledgeStage.KNOWLEDGE_BASE, KnowledgeArtifact.QUERY_CONTRACT),
                    (TargetStudyStage.STUDY, TargetStudyArtifact.STRUCTURED_PROFILE),
                    (TargetStudyStage.STUDY, TargetStudyArtifact.API_EVIDENCE),
                    (TargetStudyStage.STUDY, TargetStudyArtifact.ANALOGOUS_DRIVER_TRACE),
                    (TargetStudyStage.STUDY, TargetStudyArtifact.CHANGE_PLAN),
                    (SourceAnalysisStage.SOURCE_CLOSURE, SourceAnalysisArtifact.SOURCE_CLOSURE),
                    (SourceAnalysisStage.SOURCE_CLOSURE, SourceAnalysisArtifact.MATERIALS_MANIFEST),
                    (
                        SourceAnalysisStage.STRUCTURED_C_ANALYSIS,
                        SourceAnalysisArtifact.STRUCTURED_C_FACTS,
                    ),
                ),
            ),
            self._accept_contracts_result,
        )

    @staticmethod
    def _accept_contracts_result(project: Project, job: ArtifactOccurrence) -> None:
        MigrationContractService().finalize(
            project,
            MigrationContractSet.read(project.artifacts.path_for_digest(job.digest)),
        )

    def _test_adaptation(self, project: Project) -> None:
        self._codex_gate(
            project,
            MigrationStage.TEST_ADAPTATION,
            TEST_ADAPTATION_OBJECTIVE,
            self._migration_context(
                project,
                (
                    (MigrationStage.HANDOFF, MigrationArtifact.HANDOFF),
                    (MigrationStage.CONTRACTS, MigrationArtifact.CONTRACTS),
                    (KnowledgeStage.KNOWLEDGE_BASE, KnowledgeArtifact.QUERY_CONTRACT),
                    (TargetStudyStage.STUDY, TargetStudyArtifact.STRUCTURED_PROFILE),
                    (TargetStudyStage.STUDY, TargetStudyArtifact.API_EVIDENCE),
                    (SourceAnalysisStage.SOURCE_CLOSURE, SourceAnalysisArtifact.SOURCE_CLOSURE),
                    (SourceAnalysisStage.SOURCE_CLOSURE, SourceAnalysisArtifact.MATERIALS_MANIFEST),
                ),
            ),
            self._accept_test_adaptation_result,
        )

    @staticmethod
    def _accept_test_adaptation_result(project: Project, job: ArtifactOccurrence) -> None:
        TestSelectionService().finalize(
            project,
            TestSelectionMatrix.read(project.artifacts.path_for_digest(job.digest)),
        )

    def _implementation(self, project: Project) -> None:
        facts = project.load_json_artifact(
            SourceAnalysisStage.STRUCTURED_C_ANALYSIS, SourceAnalysisArtifact.STRUCTURED_C_FACTS
        )
        self._codex_gate(
            project,
            MigrationStage.DRIVER_IMPLEMENTATION,
            DRIVER_IMPLEMENTATION_OBJECTIVE,
            self._migration_context(
                project,
                (
                    (MigrationStage.HANDOFF, MigrationArtifact.HANDOFF),
                    (MigrationStage.CONTRACTS, MigrationArtifact.CONTRACTS),
                    (MigrationStage.TEST_ADAPTATION, MigrationArtifact.TEST_PORT_MATRIX),
                    (KnowledgeStage.KNOWLEDGE_BASE, KnowledgeArtifact.QUERY_CONTRACT),
                    (KnowledgeStage.KNOWLEDGE_BASE, KnowledgeArtifact.GENERATED_SKILL),
                    (TargetStudyStage.STUDY, TargetStudyArtifact.STRUCTURED_PROFILE),
                    (TargetStudyStage.STUDY, TargetStudyArtifact.API_EVIDENCE),
                    (TargetStudyStage.STUDY, TargetStudyArtifact.ANALOGOUS_DRIVER_TRACE),
                    (TargetStudyStage.STUDY, TargetStudyArtifact.CHANGE_PLAN),
                    (SourceAnalysisStage.SOURCE_CLOSURE, SourceAnalysisArtifact.SOURCE_CLOSURE),
                    (
                        SourceAnalysisStage.STRUCTURED_C_ANALYSIS,
                        SourceAnalysisArtifact.STRUCTURED_C_FACTS,
                    ),
                ),
                extra={
                    "semantic_indexes": [
                        {
                            "unit_id": unit["unit_id"],
                            "path": str((project.root / unit["semantic_index"]["path"]).resolve()),
                            "sha256": unit["semantic_index"]["sha256"],
                        }
                        for unit in facts["units"]
                    ]
                },
            ),
            self._accept_implementation_result,
        )

    @staticmethod
    def _accept_implementation_result(project: Project, job: ArtifactOccurrence) -> None:
        DriverImplementationService().finalize(
            project,
            ImplementationResponse.read(project.artifacts.path_for_digest(job.digest)),
        )

    def _compliance(self, project: Project) -> None:
        self._codex_gate(
            project,
            MigrationStage.TARGET_COMPLIANCE,
            TARGET_COMPLIANCE_AND_ARTIFACT_OBJECTIVE,
            self._migration_context(
                project,
                (
                    (MigrationStage.HANDOFF, MigrationArtifact.HANDOFF),
                    (MigrationStage.DRIVER_IMPLEMENTATION, MigrationArtifact.IMPLEMENTATION_BUNDLE),
                    (MigrationStage.DRIVER_IMPLEMENTATION, MigrationArtifact.TRANSLATION_COVERAGE),
                    (
                        MigrationStage.DRIVER_IMPLEMENTATION,
                        MigrationArtifact.TARGET_CHANGE_INVENTORY,
                    ),
                    (KnowledgeStage.KNOWLEDGE_BASE, KnowledgeArtifact.QUERY_CONTRACT),
                    (KnowledgeStage.KNOWLEDGE_BASE, KnowledgeArtifact.GENERATED_SKILL),
                    (TargetStudyStage.STUDY, TargetStudyArtifact.STRUCTURED_PROFILE),
                    (TargetStudyStage.STUDY, TargetStudyArtifact.API_EVIDENCE),
                    (TargetStudyStage.STUDY, TargetStudyArtifact.ANALOGOUS_DRIVER_TRACE),
                    (TargetStudyStage.STUDY, TargetStudyArtifact.CHANGE_PLAN),
                    (SourceAnalysisStage.SOURCE_CLOSURE, SourceAnalysisArtifact.MATERIALS_MANIFEST),
                    (EnvironmentStage.RECOVERY, EnvironmentArtifact.MODE_RECORD),
                    (EnvironmentStage.RECOVERY, EnvironmentArtifact.EXPERIMENT_ROUTE),
                ),
            ),
            self._accept_compliance_result,
        )

    def _accept_compliance_result(self, project: Project, job: ArtifactOccurrence) -> None:
        parts = self._write_response_parts(
            project,
            MigrationStage.TARGET_COMPLIANCE,
            job,
            ("compliance_report", "artifact_preparation_plan"),
        )
        plan, _ = ArtifactPreparationPlan.read(parts["artifact_preparation_plan"])
        ComplianceService().finalize(
            project,
            ComplianceReport.read(parts["compliance_report"]),
            plan,
        )

    def _artifact_preparation(self, project: Project) -> None:
        plan = project.artifact(
            MigrationStage.TARGET_COMPLIANCE,
            MigrationArtifact.ARTIFACT_PREPARATION_PLAN,
        )
        ArtifactPreparationService().run(project, project.artifacts.path_for_digest(plan.digest))

    def _public_qemu(self, project: Project) -> None:
        artifact = project.artifact(
            MigrationStage.ARTIFACT_PREPARATION, MigrationArtifact.RUNTIME_ARTIFACT
        )
        identity = project.load_json_artifact(
            MigrationStage.ARTIFACT_PREPARATION, MigrationArtifact.ARTIFACT_IDENTITY
        )
        self._codex_gate(
            project,
            MigrationStage.PUBLIC_QEMU_VALIDATION,
            PUBLIC_QEMU_OBJECTIVE,
            self._migration_context(
                project,
                (
                    (MigrationStage.HANDOFF, MigrationArtifact.HANDOFF),
                    (MigrationStage.CONTRACTS, MigrationArtifact.CONTRACTS),
                    (MigrationStage.TEST_ADAPTATION, MigrationArtifact.TEST_PORT_MATRIX),
                    (MigrationStage.DRIVER_IMPLEMENTATION, MigrationArtifact.IMPLEMENTATION_BUNDLE),
                    (MigrationStage.TARGET_COMPLIANCE, MigrationArtifact.COMPLIANCE_REPORT),
                    (MigrationStage.ARTIFACT_PREPARATION, MigrationArtifact.RUNTIME_ARTIFACT),
                    (MigrationStage.ARTIFACT_PREPARATION, MigrationArtifact.ARTIFACT_IDENTITY),
                    (EnvironmentStage.RECOVERY, EnvironmentArtifact.EXPERIMENT_READY_RUN),
                    (EnvironmentStage.RECOVERY, EnvironmentArtifact.EXPERIMENT_ROUTE),
                    (KnowledgeStage.KNOWLEDGE_BASE, KnowledgeArtifact.QUERY_CONTRACT),
                    (SourceAnalysisStage.SOURCE_CLOSURE, SourceAnalysisArtifact.MATERIALS_MANIFEST),
                ),
                extra={
                    "runtime_artifact_path": str(
                        project.artifacts.path_for_digest(artifact.digest)
                    ),
                    "runtime_artifact_sha256": artifact.digest,
                    "implementation_sha256": identity["inputs"][
                        MigrationArtifact.IMPLEMENTATION_BUNDLE.value
                    ]["digest"],
                    "packaged_test_sha256": identity["packaged_test_artifact"]["sha256"],
                },
            ),
            self._accept_public_qemu_result,
        )
        if project.stage(MigrationStage.PUBLIC_QEMU_VALIDATION).status is StageStatus.RUNNING:
            PublicRepairService().prepare(project)

    @staticmethod
    def _accept_public_qemu_result(project: Project, job: ArtifactOccurrence) -> None:
        PublicQemuService().run(
            project,
            PublicQemuPlan.read(project.artifacts.path_for_digest(job.digest)),
        )

    def _public_repair(self, project: Project) -> None:
        service = PublicRepairService()
        prepared = service.prepare(project)
        if prepared is None:
            service.finalize_not_applicable(project)
            return
        source_ref, failure = prepared
        bundle = project.load_json_artifact(
            MigrationStage.DRIVER_IMPLEMENTATION, MigrationArtifact.IMPLEMENTATION_BUNDLE
        )
        _, _, response = self._codex(
            project,
            MigrationStage.PUBLIC_REPAIR,
            PUBLIC_REPAIR_OBJECTIVE,
            {
                "failed_attempt": source_ref,
                "failed_evidence": failure,
                "implementation_files": [
                    {"path": item["path"], "role": item["role"]} for item in bundle["files"]
                ],
                "frozen_inputs": {
                    kind.value: self._artifact_context(project, stage, kind)
                    for stage, kind in (
                        (MigrationStage.CONTRACTS, MigrationArtifact.CONTRACTS),
                        (MigrationStage.TEST_ADAPTATION, MigrationArtifact.TEST_PORT_MATRIX),
                        (
                            MigrationStage.DRIVER_IMPLEMENTATION,
                            MigrationArtifact.IMPLEMENTATION_BUNDLE,
                        ),
                        (MigrationStage.ARTIFACT_PREPARATION, MigrationArtifact.ARTIFACT_IDENTITY),
                        (KnowledgeStage.KNOWLEDGE_BASE, KnowledgeArtifact.QUERY_CONTRACT),
                        (TargetStudyStage.STUDY, TargetStudyArtifact.CHANGE_PLAN),
                    )
                },
            },
        )
        service.run(project, PublicRepairPlan.read(response), source_ref, failure)

    @staticmethod
    def _completion_audit(project: Project) -> None:
        CompletionAuditService().run(project)

    def _migration_context(
        self,
        project: Project,
        inputs: tuple[tuple[StageKey, ArtifactKey], ...],
        *,
        extra: dict[str, object] | None = None,
    ) -> dict[str, object]:
        context: dict[str, object] = {
            "frozen_inputs": {
                kind.value: self._artifact_context(project, owner, kind) for owner, kind in inputs
            }
        }
        context.update(extra or {})
        return context


def _default_skill_root() -> Path:
    codex_home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
    return codex_home / "skills"


def command_port_run(arguments: argparse.Namespace) -> None:
    outcome = PortRunner(
        PortOptions(
            workspace=Path(arguments.workspace),
            source_platform=arguments.source_platform,
            target_platform=arguments.target_platform,
            driver_name=arguments.driver_name,
            skill_root=Path(arguments.skill_root),
            catalogs=tuple(Path(path).resolve() for path in arguments.catalog),
            backend=arguments.backend,
            codex_bin=arguments.codex_bin,
            model=arguments.model,
            analyzer=arguments.analyzer,
            analyzer_family=arguments.analyzer_family,
        )
    ).run()
    print(json.dumps(outcome.to_dict(), ensure_ascii=False, sort_keys=True, indent=2))


def register_commands(commands: CommandRegistry) -> None:
    port = commands.add_parser("port", help="run or resume the complete developer migration")
    port_commands = command_registry(port, dest="port_command")
    run = port_commands.add_parser("run")
    run.add_argument("workspace")
    run.add_argument("--source-platform", required=True)
    run.add_argument("--target-platform", required=True)
    run.add_argument("--driver-name", required=True)
    run.add_argument("--skill-root", default=str(_default_skill_root()))
    run.add_argument("--catalog", action="append", default=[])
    run.add_argument(
        "--backend", type=CodexBackend, choices=list(CodexBackend), default=CodexBackend.EXEC
    )
    run.add_argument("--codex-bin", default="codex")
    run.add_argument("--model")
    run.add_argument("--analyzer", default="clang")
    run.add_argument(
        "--analyzer-family",
        type=AnalyzerFamily,
        choices=list(AnalyzerFamily),
        default=AnalyzerFamily.CLANG_LLVM,
    )
    run.set_defaults(handler=command_port_run)
