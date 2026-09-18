from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .acquisition.closure import EvidenceClosureFinalizer
from .acquisition.contracts import AcquisitionArtifact, AcquisitionStage
from .acquisition.job import ArtifactOccurrence
from .acquisition.proposal import (
    CODEX_EVIDENCE_SELECTION_OBJECTIVE,
    EvidenceProposalImporter,
)
from .acquisition.repository import RepositoryAcquirer, load_repository_acquisition
from .acquisition.repository_role import RepositoryRole
from .acquisition.revision_proposal import RevisionProposalImporter
from .acquisition.revision_selection import RevisionSelector
from .cli_support import CommandRegistry, command_registry
from .codex.cli import run_codex_stage
from .codex.contracts import CodexArtifact, CodexBackend, CodexOutputError
from .codex.policy import CodexExecutionPolicy
from .composition import initialize_project, open_project
from .core.contracts import ArtifactKey, StageKey
from .core.models import (
    ActorRole,
    ArtifactDirection,
    EvaluationMode,
    FileArtifact,
    ProjectConfig,
    StageStatus,
    WorkflowError,
)
from .core.project import Project
from .environment.contracts import EnvironmentArtifact, EnvironmentStage
from .environment.execution import ExperimentExecutor
from .environment.inventory import EnvironmentInspector
from .environment.models import ExperimentReadiness
from .intake.contracts import IntakeArtifact, IntakeStage
from .intake.service import IntakeService
from .knowledge.bootstrap import KnowledgeBootstrapper
from .knowledge.contracts import KnowledgeArtifact, KnowledgeStage
from .migration.artifact_preparation import ArtifactPreparationService
from .migration.completion_audit import CompletionAuditService
from .migration.contracts import MigrationArtifact, MigrationStage
from .migration.handoff import MigrationHandoff
from .migration.implementation import DriverImplementationService
from .migration.public_qemu import PublicQemuService
from .migration.public_repair import PublicRepairService
from .source_analysis.clang_backend import AnalyzerFamily
from .source_analysis.closure import SourceClosureService
from .source_analysis.contracts import SourceAnalysisArtifact, SourceAnalysisStage
from .source_analysis.structured import StructuredCAnalysisService
from .target_study.contracts import TargetStudyArtifact, TargetStudyStage


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
        review_retries = 0
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
            if (
                stage.name
                in {
                    MigrationStage.TARGET_COMPLIANCE,
                    MigrationStage.PUBLIC_REPAIR,
                    SourceAnalysisStage.STRUCTURED_C_ANALYSIS,
                }
                and current.status is StageStatus.PENDING
            ):
                review_retries += 1
                if review_retries >= 3:
                    return PortOutcome(
                        stage.name.value,
                        current.status,
                        "review repair budget reached; inspect preserved findings",
                    )
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
        context: dict[str, object],
        *,
        objective: str | None = None,
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
            skill_root=self.options.skill_root.resolve(),
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
            "kind": reference.kind,
            "digest": reference.digest,
            "path": str(project.artifacts.path_for_digest(reference.digest)),
            "size_bytes": reference.size,
        }

    @staticmethod
    def _job_occurrence(
        project: Project, stage: StageKey, response_path: Path
    ) -> ArtifactOccurrence:
        matches = [
            ref
            for ref in project.current_artifact_refs(
                stage=stage,
                direction=ArtifactDirection.OUTPUT,
            )
            if ref.kind == CodexArtifact.JOB_RESULT.value and ref.source == str(response_path)
        ]
        if len(matches) != 1 or matches[0].ordinal is None:
            raise WorkflowError("Codex result occurrence was not persisted uniquely")
        return ArtifactOccurrence(matches[0].digest, matches[0].ordinal)

    @staticmethod
    def _latest_job_occurrence(project: Project, stage: StageKey) -> ArtifactOccurrence | None:
        matches = [
            ref
            for ref in project.current_artifact_refs(
                stage=stage,
                direction=ArtifactDirection.OUTPUT,
            )
            if ref.kind == CodexArtifact.JOB_RESULT.value and ref.ordinal is not None
        ]
        if not matches:
            return None
        latest = max(matches, key=lambda ref: ref.ordinal)
        return ArtifactOccurrence(latest.digest, latest.ordinal)

    @staticmethod
    def _latest_persisted_job_occurrence(
        project: Project, stage: StageKey
    ) -> ArtifactOccurrence | None:
        matches = [
            ref
            for ref in project.artifact_refs(
                stage=stage,
                direction=ArtifactDirection.OUTPUT,
            )
            if ref.kind == CodexArtifact.JOB_RESULT.value and ref.ordinal is not None
        ]
        if not matches:
            return None
        latest = max(matches, key=lambda ref: ref.ordinal)
        return ArtifactOccurrence(latest.digest, latest.ordinal)

    def _codex_gate(
        self,
        project: Project,
        stage: StageKey,
        context: dict[str, object],
        accept: Callable[[Project, ArtifactOccurrence], None],
        *,
        objective: str | None = None,
    ) -> bool:
        pending = self._latest_job_occurrence(project, stage)
        thread_id = self._latest_thread_id(project, stage)
        feedback = None
        seen_errors = set()
        for _ in range(3):
            if pending is None:
                result, _, response = self._codex(
                    project,
                    stage,
                    context,
                    objective=objective,
                    thread_id=thread_id,
                    follow_up=feedback,
                )
                thread_id = result.thread_id
                pending = self._job_occurrence(project, stage, response)
            try:
                accept(project, pending)
                return True
            except CodexOutputError as error:
                feedback = str(error)
                if feedback in seen_errors:
                    raise
                seen_errors.add(feedback)
                pending = None
        raise CodexOutputError(f"bounded correction exhausted: {feedback}")

    @staticmethod
    def _latest_thread_id(project: Project, stage: StageKey) -> str | None:
        # Gateway session identity also checks role, cwd, sandbox, model and provider.
        # Never infer a thread from an unscoped historical event log here.
        return None

    @staticmethod
    def _materialize_codex_report(
        project: Project,
        stage: StageKey,
        job: ArtifactOccurrence,
    ) -> Path:
        matches = [
            ref
            for ref in project.current_artifact_refs(
                stage=stage,
                direction=ArtifactDirection.OUTPUT,
            )
            if ref.kind == CodexArtifact.JOB_RESULT.value
            and ref.digest == job.digest
            and ref.ordinal == job.ordinal
        ]
        if len(matches) != 1:
            raise WorkflowError("Codex result occurrence was not persisted uniquely")
        data = project.artifacts.read(matches[0])
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as error:
            raise WorkflowError("Codex work report is not UTF-8") from error
        if not text.strip():
            raise WorkflowError("Codex work report is blank")
        report_paths = re.findall(r"^REPORT_PATH:\s*(.+\.md)\s*$", text, re.MULTILINE)
        if not report_paths:
            report_paths = re.findall(r"\]\((/[^\n)]+\.md)\)", text)
        if len(set(report_paths)) == 1:
            root = CodexExecutionPolicy().grant(project, stage).execution_root.resolve()
            report_path = (root / report_paths[0].strip()).resolve()
            if root not in report_path.parents or not report_path.is_file():
                raise CodexOutputError(
                    "report path must name a Markdown file in the stage workspace"
                )
            data = report_path.read_bytes()
            if not data.decode("utf-8").strip():
                raise CodexOutputError("report file is empty")
        output = (
            project.root
            / "work"
            / "stage-reports"
            / stage.value
            / f"{job.ordinal:04d}-{job.digest[:16]}.md"
        )
        output.parent.mkdir(parents=True, exist_ok=True)
        if output.exists() and output.read_bytes() != data:
            raise WorkflowError("materialized Codex work report changed")
        if not output.exists():
            output.write_bytes(data)
        return output

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
            {"migration_envelope": envelope},
            self._accept_revision_result,
        )

    @staticmethod
    def _accept_revision_result(project: Project, job: ArtifactOccurrence) -> None:
        try:
            proposal = RevisionProposalImporter().import_job_result(
                project, job_digest=job.digest, job_ordinal=job.ordinal
            )
            RevisionSelector().select(project, proposal=proposal)
        except CodexOutputError:
            raise
        except WorkflowError as error:
            raise CodexOutputError(f"revision proposal failed: {error}") from error

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
            inputs,
            self._accept_evidence_result,
            objective=CODEX_EVIDENCE_SELECTION_OBJECTIVE,
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

    def _accept_environment_result(self, project: Project, job: ArtifactOccurrence) -> None:
        report = self._materialize_codex_report(project, EnvironmentStage.RECOVERY, job)
        result = ExperimentExecutor().run_codex_harness(
            project,
            script_path=(
                project.root
                / "work"
                / "stage-work"
                / EnvironmentStage.RECOVERY.value
                / "environment-smoke.sh"
            ),
            work_report_path=report,
        )
        if result.readiness is ExperimentReadiness.FAIL:
            raise WorkflowError(f"{result.message}; attempt: {result.attempt_path}")

    def _knowledge(self, project: Project) -> None:
        KnowledgeBootstrapper().build_infrastructure(project)

    def _target_study(self, project: Project) -> None:
        acquisition = load_repository_acquisition(project)
        target = acquisition.checkout(RepositoryRole.TARGET)
        context: dict[str, object] = {
            "target_repository": {
                "path": str((project.root / target.checkout_path).resolve()),
                "revision": target.resolved_commit,
            },
            "knowledge": self._artifact_context(
                project, KnowledgeStage.KNOWLEDGE_BASE, KnowledgeArtifact.QUERY_CONTRACT
            ),
            "knowledge_skill": self._artifact_context(
                project, KnowledgeStage.KNOWLEDGE_BASE, KnowledgeArtifact.GENERATED_SKILL
            ),
        }
        self._codex_gate(
            project,
            TargetStudyStage.STUDY,
            context,
            self._accept_target_study_result,
        )

    def _accept_target_study_result(self, project: Project, job: ArtifactOccurrence) -> None:
        report = self._materialize_codex_report(project, TargetStudyStage.STUDY, job)
        project.finalize_stage(
            TargetStudyStage.STUDY,
            tuple(
                FileArtifact(kind, report)
                for kind in (
                    TargetStudyArtifact.PROFILE,
                    TargetStudyArtifact.STRUCTURED_PROFILE,
                    TargetStudyArtifact.API_EVIDENCE,
                    TargetStudyArtifact.ANALOGOUS_DRIVER_TRACE,
                    TargetStudyArtifact.CHANGE_PLAN,
                    TargetStudyArtifact.REPORT,
                )
            ),
        )

    @staticmethod
    def _handoff(project: Project) -> None:
        MigrationHandoff().create(project)

    def _source_closure(self, project: Project) -> None:
        attempts = [
            ref
            for ref in project.artifact_refs(stage=SourceAnalysisStage.STRUCTURED_C_ANALYSIS)
            if ref.kind == SourceAnalysisArtifact.STRUCTURED_C_ANALYSIS_ATTEMPT.value
        ]
        self._codex_gate(
            project,
            SourceAnalysisStage.SOURCE_CLOSURE,
            {
                "structured_analyzer": self.options.analyzer,
                "analysis_feedback_path": (
                    str(project.artifacts.path_for_digest(attempts[-1].digest))
                    if attempts
                    else None
                ),
                "migration_envelope": self._artifact_context(
                    project, IntakeStage.ENVELOPE_FREEZE, IntakeArtifact.MIGRATION_ENVELOPE
                ),
                "repository_manifest": self._artifact_context(
                    project,
                    AcquisitionStage.REPOSITORY_ACQUISITION,
                    AcquisitionArtifact.REPOSITORY_MANIFEST,
                ),
                "materials_manifest": self._artifact_context(
                    project,
                    AcquisitionStage.EVIDENCE_CLOSURE,
                    AcquisitionArtifact.MATERIALS_MANIFEST,
                ),
                "evidence_gap_register": self._artifact_context(
                    project,
                    AcquisitionStage.EVIDENCE_CLOSURE,
                    AcquisitionArtifact.EVIDENCE_GAP_REGISTER,
                ),
                "knowledge_query_contract": self._artifact_context(
                    project,
                    KnowledgeStage.KNOWLEDGE_BASE,
                    KnowledgeArtifact.QUERY_CONTRACT,
                ),
            },
            self._accept_source_closure_result,
        )

    def _accept_source_closure_result(self, project: Project, job: ArtifactOccurrence) -> None:
        report = self._materialize_codex_report(project, SourceAnalysisStage.SOURCE_CLOSURE, job)
        try:
            result = SourceClosureService().finalize_compilation_database(
                project,
                compilation_database_path=(
                    project.root
                    / "work"
                    / "stage-work"
                    / "source_closure"
                    / "compile_commands.json"
                ),
                work_report_path=report,
            )
        except WorkflowError as error:
            message = str(error)
            actionable = (
                "compile command",
                "compile argv",
                "compiler dependency scan failed",
                "compile_commands.json",
                "source compiler is unavailable",
            )
            if any(reason in message for reason in actionable):
                raise CodexOutputError(message) from error
            raise
        if result.errors:
            raise CodexOutputError("source closure failed: " + "; ".join(result.errors))

    def _structured_c(self, project: Project) -> None:
        result = StructuredCAnalysisService().analyze(
            project,
            analyzer=self.options.analyzer,
            analyzer_family=self.options.analyzer_family,
        )
        if result.errors:
            if not any("extraction failed:" in error or "target ABI differs" in error
                       for error in result.errors):
                raise WorkflowError("structured analysis tool failure: " + "; ".join(result.errors))
            project.retry_from(
                SourceAnalysisStage.SOURCE_CLOSURE,
                trigger=SourceAnalysisStage.STRUCTURED_C_ANALYSIS,
                reason=f"repair compile inputs after analysis failure: {result.report_path}",
            )

    def _contracts(self, project: Project) -> None:
        self._codex_gate(
            project,
            MigrationStage.CONTRACTS,
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

    def _accept_contracts_result(self, project: Project, job: ArtifactOccurrence) -> None:
        report = self._materialize_codex_report(project, MigrationStage.CONTRACTS, job)
        project.finalize_stage(
            MigrationStage.CONTRACTS,
            (FileArtifact(MigrationArtifact.CONTRACTS, report),),
        )

    def _test_adaptation(self, project: Project) -> None:
        self._codex_gate(
            project,
            MigrationStage.TEST_ADAPTATION,
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

    def _accept_test_adaptation_result(self, project: Project, job: ArtifactOccurrence) -> None:
        report = self._materialize_codex_report(project, MigrationStage.TEST_ADAPTATION, job)
        project.finalize_stage(
            MigrationStage.TEST_ADAPTATION,
            (FileArtifact(MigrationArtifact.TEST_PORT_MATRIX, report),),
        )

    def _implementation(self, project: Project) -> None:
        facts = project.load_json_artifact(
            SourceAnalysisStage.STRUCTURED_C_ANALYSIS, SourceAnalysisArtifact.STRUCTURED_C_FACTS
        )
        extra: dict[str, object] = {
            "semantic_indexes": [
                {
                    "unit_id": unit["unit_id"],
                    "path": str((project.root / unit["semantic_index"]["path"]).resolve()),
                    "sha256": unit["semantic_index"]["sha256"],
                }
                for unit in facts["units"]
            ]
        }
        review = self._latest_persisted_job_occurrence(project, MigrationStage.TARGET_COMPLIANCE)
        if review:
            extra["review_feedback_path"] = str(project.artifacts.path_for_digest(review.digest))
        runtime_reports = [
            ref for ref in project.artifact_refs(stage=MigrationStage.PUBLIC_QEMU_VALIDATION)
            if ref.kind == MigrationArtifact.PUBLIC_QEMU_REPORT.value
        ]
        if runtime_reports:
            latest = max(runtime_reports, key=lambda ref: ref.ordinal or 0)
            if json.loads(project.artifacts.read(latest)).get("execution_status") != "PASS":
                extra["runtime_failure_path"] = str(
                    project.artifacts.path_for_digest(latest.digest)
                )
        runtime_review = self._latest_persisted_job_occurrence(
            project, MigrationStage.PUBLIC_REPAIR
        )
        if runtime_review:
            extra["runtime_review_path"] = str(
                project.artifacts.path_for_digest(runtime_review.digest)
            )
        self._codex_gate(
            project,
            MigrationStage.DRIVER_IMPLEMENTATION,
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
                extra=extra,
            ),
            self._accept_implementation_result,
        )

    def _accept_implementation_result(self, project: Project, job: ArtifactOccurrence) -> None:
        report = self._materialize_codex_report(project, MigrationStage.DRIVER_IMPLEMENTATION, job)
        DriverImplementationService().snapshot_worktree(project, report)

    def _compliance(self, project: Project) -> None:
        self._codex_gate(
            project,
            MigrationStage.TARGET_COMPLIANCE,
            self._migration_context(
                project,
                (
                    (MigrationStage.DRIVER_IMPLEMENTATION, MigrationArtifact.IMPLEMENTATION_BUNDLE),
                    (MigrationStage.DRIVER_IMPLEMENTATION, MigrationArtifact.TRANSLATION_COVERAGE),
                    (MigrationStage.CONTRACTS, MigrationArtifact.CONTRACTS),
                    (MigrationStage.TEST_ADAPTATION, MigrationArtifact.TEST_PORT_MATRIX),
                    (KnowledgeStage.KNOWLEDGE_BASE, KnowledgeArtifact.QUERY_CONTRACT),
                    (TargetStudyStage.STUDY, TargetStudyArtifact.PROFILE),
                    (
                        SourceAnalysisStage.STRUCTURED_C_ANALYSIS,
                        SourceAnalysisArtifact.STRUCTURED_C_FACTS,
                    ),
                ),
            ),
            self._accept_compliance_result,
        )

    def _accept_compliance_result(self, project: Project, job: ArtifactOccurrence) -> None:
        report = self._materialize_codex_report(project, MigrationStage.TARGET_COMPLIANCE, job)
        verdict = report.read_text().strip().splitlines()[-1]
        if verdict not in {"DPF_REVIEW: PASS", "DPF_REVIEW: REWORK"}:
            raise CodexOutputError("review must end with DPF_REVIEW: PASS or DPF_REVIEW: REWORK")
        if verdict == "DPF_REVIEW: REWORK":
            project.retry_from(
                MigrationStage.DRIVER_IMPLEMENTATION,
                trigger=MigrationStage.TARGET_COMPLIANCE,
                reason=f"review requires repair; feedback: {report}",
            )
            return
        project.finalize_stage(
            MigrationStage.TARGET_COMPLIANCE,
            (FileArtifact(MigrationArtifact.COMPLIANCE_REPORT, report),),
        )

    def _artifact_preparation(self, project: Project) -> None:
        self._codex_gate(
            project,
            MigrationStage.ARTIFACT_PREPARATION,
            self._migration_context(
                project,
                (
                    (MigrationStage.HANDOFF, MigrationArtifact.HANDOFF),
                    (MigrationStage.DRIVER_IMPLEMENTATION, MigrationArtifact.IMPLEMENTATION_BUNDLE),
                    (MigrationStage.TARGET_COMPLIANCE, MigrationArtifact.COMPLIANCE_REPORT),
                    (EnvironmentStage.RECOVERY, EnvironmentArtifact.MODE_RECORD),
                    (EnvironmentStage.RECOVERY, EnvironmentArtifact.EXPERIMENT_ROUTE),
                    (TargetStudyStage.STUDY, TargetStudyArtifact.STRUCTURED_PROFILE),
                ),
            ),
            self._accept_artifact_preparation_result,
        )

    def _accept_artifact_preparation_result(
        self, project: Project, job: ArtifactOccurrence
    ) -> None:
        report = self._materialize_codex_report(project, MigrationStage.ARTIFACT_PREPARATION, job)
        ArtifactPreparationService().capture_codex_artifact(project, report)

    def _public_qemu(self, project: Project) -> None:
        artifact = project.artifact(
            MigrationStage.ARTIFACT_PREPARATION, MigrationArtifact.RUNTIME_ARTIFACT
        )
        accepted = self._codex_gate(
            project,
            MigrationStage.PUBLIC_QEMU_VALIDATION,
            self._migration_context(
                project,
                (
                    (MigrationStage.HANDOFF, MigrationArtifact.HANDOFF),
                    (MigrationStage.CONTRACTS, MigrationArtifact.CONTRACTS),
                    (MigrationStage.TEST_ADAPTATION, MigrationArtifact.TEST_PORT_MATRIX),
                    (
                        MigrationStage.DRIVER_IMPLEMENTATION,
                        MigrationArtifact.TRANSLATION_COVERAGE,
                    ),
                    (MigrationStage.ARTIFACT_PREPARATION, MigrationArtifact.RUNTIME_ARTIFACT),
                    (MigrationStage.ARTIFACT_PREPARATION, MigrationArtifact.ARTIFACT_IDENTITY),
                    (EnvironmentStage.RECOVERY, EnvironmentArtifact.EXPERIMENT_ROUTE),
                    (KnowledgeStage.KNOWLEDGE_BASE, KnowledgeArtifact.QUERY_CONTRACT),
                    (SourceAnalysisStage.SOURCE_CLOSURE, SourceAnalysisArtifact.MATERIALS_MANIFEST),
                ),
                extra={
                    "runtime_artifact_path": str(
                        project.artifacts.path_for_digest(artifact.digest)
                    ),
                    "runtime_artifact_sha256": artifact.digest,
                },
            ),
            self._accept_public_qemu_result,
        )
        if not accepted:
            return
        if project.stage(MigrationStage.PUBLIC_QEMU_VALIDATION).status is StageStatus.RUNNING:
            PublicRepairService().prepare(project)

    def _accept_public_qemu_result(self, project: Project, job: ArtifactOccurrence) -> None:
        report = self._materialize_codex_report(project, MigrationStage.PUBLIC_QEMU_VALIDATION, job)
        acquisition = load_repository_acquisition(project)
        worktree = (project.root / acquisition.target_worktree.path).resolve()
        PublicQemuService().run_script(
            project,
            script_path=worktree / ".dpf-output" / "public-qemu.sh",
            work_report_path=report,
        )

    def _public_repair(self, project: Project) -> None:
        service = PublicRepairService()
        prepared = service.prepare(project)
        if prepared is None:
            self._codex_gate(
                project, MigrationStage.PUBLIC_REPAIR,
                self._migration_context(project, (
                    (MigrationStage.DRIVER_IMPLEMENTATION, MigrationArtifact.IMPLEMENTATION_BUNDLE),
                    (MigrationStage.CONTRACTS, MigrationArtifact.CONTRACTS),
                    (MigrationStage.TEST_ADAPTATION, MigrationArtifact.TEST_PORT_MATRIX),
                    (MigrationStage.TARGET_COMPLIANCE, MigrationArtifact.COMPLIANCE_REPORT),
                    (MigrationStage.ARTIFACT_PREPARATION, MigrationArtifact.ARTIFACT_IDENTITY),
                    (MigrationStage.PUBLIC_QEMU_VALIDATION, MigrationArtifact.PUBLIC_QEMU_REPORT),
                )),
                self._accept_runtime_review,
            )
            return
        source_ref, _ = prepared
        if project.stage(MigrationStage.PUBLIC_REPAIR).status is StageStatus.READY:
            project.start(MigrationStage.PUBLIC_REPAIR)
        project.retry_from(
            MigrationStage.DRIVER_IMPLEMENTATION,
            trigger=MigrationStage.PUBLIC_REPAIR,
            reason=("runtime failure returns to the existing implementation conversation: "
                    f"{project.artifacts.path_for_digest(source_ref['digest'])}"),
        )

    def _accept_runtime_review(self, project: Project, job: ArtifactOccurrence) -> None:
        report = self._materialize_codex_report(project, MigrationStage.PUBLIC_REPAIR, job)
        verdict = report.read_text().strip().splitlines()[-1]
        if verdict not in {"DPF_REVIEW: PASS", "DPF_REVIEW: REWORK"}:
            raise CodexOutputError("review must end with DPF_REVIEW: PASS or DPF_REVIEW: REWORK")
        if verdict == "DPF_REVIEW: REWORK":
            project.retry_from(
                MigrationStage.DRIVER_IMPLEMENTATION, trigger=MigrationStage.PUBLIC_REPAIR,
                reason=f"runtime evidence review requires rework: {report}",
            )
            return
        PublicRepairService().finalize_not_applicable(project, review_path=report)

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
        acquisition = load_repository_acquisition(project)
        context: dict[str, object] = {
            "workspace_paths": {
                "project_root": str(project.root),
                "target_worktree": str(project.root / acquisition.target_worktree.path),
                "frozen_baselines": {
                    record.role.value: str(project.root / record.checkout_path)
                    for record in acquisition.checkouts
                },
                "knowledge_skill": self._artifact_context(
                    project, KnowledgeStage.KNOWLEDGE_BASE, KnowledgeArtifact.GENERATED_SKILL
                )["path"],
            },
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
