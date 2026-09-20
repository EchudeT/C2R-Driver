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
from .acquisition.proposal import EvidenceProposalImporter
from .acquisition.repository import RepositoryAcquirer, load_repository_acquisition
from .acquisition.repository_role import RepositoryRole
from .acquisition.revision_proposal import RevisionProposalImporter
from .acquisition.revision_selection import RevisionSelector
from .cli_support import CommandRegistry, command_registry
from .codex.cli import run_codex_stage
from .codex.contracts import CodexArtifact, CodexBackend, CodexContinuation, CodexOutputError
from .codex.policy import CodexExecutionPolicy
from .composition import initialize_project, open_project
from .core.contracts import ArtifactKey, StageKey
from .core.models import (
    ActorRole,
    ArtifactDirection,
    EvaluationMode,
    FileArtifact,
    GeneratedArtifact,
    ProjectConfig,
    StageStatus,
    WorkflowError,
)
from .core.project import Project
from .control.runtime import controller_run
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
from .migration.implementation import DriverImplementationService, ImplementationChanged
from .migration.public_qemu import PublicQemuService
from .migration.public_repair import PublicRepairService
from .migration.repair_routing import PrerequisiteRepair, WorkerBlocked, repair_target, retry_prerequisite
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
            MigrationStage.CONTRACTS: self._contracts,
            MigrationStage.DRIVER_IMPLEMENTATION: self._implementation,
            MigrationStage.ARTIFACT_PREPARATION: self._artifact_preparation,
            MigrationStage.PUBLIC_QEMU_VALIDATION: self._public_qemu,
            MigrationStage.PUBLIC_REPAIR: self._public_repair,
            MigrationStage.COMPLETION_AUDIT: self._completion_audit,
        }

    def run(self) -> PortOutcome:
        project = self._project()
        with controller_run(project):
            return self._run_project(project)

    def _run_project(self, project: Project) -> PortOutcome:
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
        corrections = 0
        while corrections < 3:
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
            except WorkerBlocked as error:
                project.complete(stage, StageStatus.BLOCKED, message=str(error))
                return False
            except PrerequisiteRepair as error:
                target = project.stage(error.target)
                if (target.status is not StageStatus.PASS or target.position >= project.stage(stage).position
                        or stage.value not in project.workflow.descendants(error.target)):
                    feedback = "Repair locally; DPF_REPAIR_STAGE must name a passed actual data prerequisite in context.repair_targets within the current phase."
                    pending = None
                    corrections += 1
                    continue
                retry_prerequisite(project, error.target, trigger=stage, reason=str(error))
                return False
            except CodexContinuation as progress:
                # Progress is deliberately not formatted as rejected output.
                context = {**context, "controller_execution": str(progress)}
                feedback = None
                pending = None
            except CodexOutputError as error:
                corrections += 1
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
        source = f"generated:codex-work-report:{job.ordinal}:{job.digest}"
        frozen = [ref for ref in project.current_artifact_refs(stage=stage)
                  if ref.kind == CodexArtifact.WORK_REPORT.value and ref.source == source]
        if frozen:
            if len(frozen) != 1:
                raise WorkflowError("Codex work report was not persisted uniquely")
            path = project.artifacts.path_for_digest(frozen[0].digest)
            PortRunner._report_outcome(path)
            return path
        data = project.artifacts.read(matches[0])
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as error:
            raise CodexOutputError("Codex work report is not UTF-8") from error
        if not text.strip():
            raise CodexOutputError("Codex work report is blank; write the report and return REPORT_PATH")
        report_paths = re.findall(r"^REPORT_PATH:\s*(.+\.md)\s*$", text, re.MULTILINE)
        if len(report_paths) != 1:
            raise CodexOutputError("Return exactly one REPORT_PATH: <absolute .md path> naming your completed report file.")
        if len(report_paths) == 1:
            root = CodexExecutionPolicy().grant(project, stage).execution_root.resolve()
            submitted = Path(report_paths[0].strip())
            if not submitted.is_absolute():
                raise CodexOutputError("REPORT_PATH must be absolute")
            report_path = submitted.resolve()
            if root not in report_path.parents or not report_path.is_file():
                raise CodexOutputError(
                    "report path must name a Markdown file in the stage workspace"
                )
            if stage in CodexExecutionPolicy.WRITABLE_STAGES and root / ".dpf-output" not in report_path.parents:
                raise CodexOutputError(
                    "Move the report into .dpf-output/ and return its absolute REPORT_PATH; "
                    "reports and runtime scripts must not become implementation changes."
                )
            data = report_path.read_bytes()
            try:
                report_text = data.decode("utf-8")
            except UnicodeDecodeError as error:
                raise CodexOutputError("report file must be UTF-8 Markdown") from error
            if not report_text.strip():
                raise CodexOutputError("report file is empty")
        frozen = project.record_artifact(stage, GeneratedArtifact(CodexArtifact.WORK_REPORT, data, source))
        path = project.artifacts.path_for_digest(frozen.digest)
        from .orchestration.protocol import operation
        try:
            operation(stage.value, report_text)
        except WorkflowError as error:
            raise CodexOutputError(str(error)) from error
        PortRunner._report_outcome(path)
        return path

    @staticmethod
    def _report_outcome(path: Path) -> None:
        text = path.read_text(encoding="utf-8").rstrip()
        if text.endswith("\nDPF_STATUS: BLOCKED"):
            raise WorkerBlocked(f"worker reported a prerequisite blocker; frozen report: {path}")
        if text.endswith("\nDPF_REVIEW: REWORK"):
            raise PrerequisiteRepair(repair_target(text), str(path))

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
        previous = [ref for ref in project.artifact_refs(stage=AcquisitionStage.EVIDENCE_CLOSURE)
                    if ref.kind == AcquisitionArtifact.EVIDENCE_CLOSURE_PLAN.value]
        if previous:
            latest = max(previous, key=lambda ref: ref.ordinal)
            inputs["previous_accepted_selection_path"] = str(project.artifacts.path_for_digest(latest.digest))
        self._codex_gate(
            project,
            AcquisitionStage.EVIDENCE_CLOSURE,
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
            raise CodexOutputError(f"{result.message}; attempt: {result.attempt_path}")

    def _knowledge(self, project: Project) -> None:
        KnowledgeBootstrapper().build_infrastructure(project)

    def _target_study(self, project: Project) -> None:
        from .target_study.reuse import restore
        repair = project.retry_feedback(TargetStudyStage.STUDY)
        if repair and repair.get("repair_root") == AcquisitionStage.EVIDENCE_CLOSURE.value:
            if restore(project):
                return
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
        from .target_study.service import TargetStudyService
        TargetStudyService().accept(project, report)
        from .target_study.reuse import remember
        remember(project)

    @staticmethod
    def _handoff(project: Project) -> None:
        MigrationHandoff().create(project)

    def _source_closure(self, project: Project) -> None:
        attempts = [
            ref
            for ref in project.artifact_refs(stage=SourceAnalysisStage.SOURCE_CLOSURE)
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
        from .source_analysis.preparation import finish, reusable
        from .orchestration.protocol import operation
        if operation(SourceAnalysisStage.SOURCE_CLOSURE.value, report.read_text()) is None:
            try:
                finish(project, report)
            except WorkflowError as error:
                raise CodexOutputError(str(error)) from error
            return
        database = project.root / "work/stage-work/source_closure/compile_commands.json"
        if database.is_file() and reusable(project, database):
            from .source_analysis.navigation import prepare_navigation
            prepare_navigation(project)
            raise CodexContinuation("Unchanged source inputs and facts verified; reuse knowledge c-facts and finish self-check.")
        try:
            result = SourceClosureService().prepare_compilation_database(
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
        result = StructuredCAnalysisService().analyze(
            project,
            analyzer=self.options.analyzer,
            analyzer_family=self.options.analyzer_family,
        )
        if result.errors:
            if not any("extraction failed:" in error or "target ABI differs" in error
                       for error in result.errors):
                raise WorkflowError("structured analysis tool failure: " + "; ".join(result.errors))
            raise CodexOutputError(f"Repair compilation inputs locally: {result.report_path}; {result.errors}")
        from .source_analysis.navigation import prepare_navigation
        prepare_navigation(project)
        raise CodexContinuation(
            "Source facts are ready. Query knowledge c-facts for selected symbols, inspect originals, "
            "and finish source behavior/coverage self-check in the same report. "
            "If closure inputs must change, request SOURCE_ANALYSIS again."
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
                        SourceAnalysisStage.SOURCE_CLOSURE,
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
            (
                FileArtifact(MigrationArtifact.CONTRACTS, report),
                FileArtifact(MigrationArtifact.TEST_PORT_MATRIX, report),
            ),
        )

    def _implementation(self, project: Project) -> None:
        from .migration.repair_execution import active, prepared
        report = prepared(project) if active(project, MigrationStage.DRIVER_IMPLEMENTATION) else None
        if report is not None:
            if project.stage(MigrationStage.DRIVER_IMPLEMENTATION).status is StageStatus.READY:
                project.start(MigrationStage.DRIVER_IMPLEMENTATION)
            DriverImplementationService().snapshot_worktree(project, report)
            return
        facts = project.load_json_artifact(
            SourceAnalysisStage.SOURCE_CLOSURE, SourceAnalysisArtifact.STRUCTURED_C_FACTS
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
        self._codex_gate(
            project,
            MigrationStage.DRIVER_IMPLEMENTATION,
            self._migration_context(
                project,
                (
                    (MigrationStage.HANDOFF, MigrationArtifact.HANDOFF),
                    (MigrationStage.CONTRACTS, MigrationArtifact.CONTRACTS),
                    (MigrationStage.CONTRACTS, MigrationArtifact.TEST_PORT_MATRIX),
                    (KnowledgeStage.KNOWLEDGE_BASE, KnowledgeArtifact.QUERY_CONTRACT),
                    (KnowledgeStage.KNOWLEDGE_BASE, KnowledgeArtifact.GENERATED_SKILL),
                    (TargetStudyStage.STUDY, TargetStudyArtifact.STRUCTURED_PROFILE),
                    (TargetStudyStage.STUDY, TargetStudyArtifact.API_EVIDENCE),
                    (TargetStudyStage.STUDY, TargetStudyArtifact.ANALOGOUS_DRIVER_TRACE),
                    (TargetStudyStage.STUDY, TargetStudyArtifact.CHANGE_PLAN),
                    (SourceAnalysisStage.SOURCE_CLOSURE, SourceAnalysisArtifact.SOURCE_CLOSURE),
                    (
                        SourceAnalysisStage.SOURCE_CLOSURE,
                        SourceAnalysisArtifact.STRUCTURED_C_FACTS,
                    ),
                ),
                extra=extra,
            ),
            self._accept_implementation_result,
        )

    def _accept_implementation_result(self, project: Project, job: ArtifactOccurrence) -> None:
        report = self._materialize_codex_report(project, MigrationStage.DRIVER_IMPLEMENTATION, job)
        from .migration.repair_execution import active, identity, record
        repair = active(project, MigrationStage.DRIVER_IMPLEMENTATION)
        if repair:
            try:
                identity(project)
            except WorkflowError as error:
                raise CodexOutputError(str(error)) from error
        if repair:
            record(project, report)
        DriverImplementationService().snapshot_worktree(project, report)

    def _artifact_preparation(self, project: Project) -> None:
        from .migration.repair_execution import prepared
        report = prepared(project)
        preparation_error = None
        if report is not None:
            if project.stage(MigrationStage.ARTIFACT_PREPARATION).status is StageStatus.READY:
                project.start(MigrationStage.ARTIFACT_PREPARATION)
            try:
                ArtifactPreparationService().capture_codex_artifact(project, report)
                return
            except CodexOutputError as error:
                # Actual failed validation needs repair work, not a fresh planning
                # turn on a successful path. Forward the concrete failed receipt.
                preparation_error = str(error)
        self._codex_gate(
            project,
            MigrationStage.ARTIFACT_PREPARATION,
            self._migration_context(
                project,
                (
                    (MigrationStage.HANDOFF, MigrationArtifact.HANDOFF),
                    (MigrationStage.CONTRACTS, MigrationArtifact.CONTRACTS),
                    (MigrationStage.CONTRACTS, MigrationArtifact.TEST_PORT_MATRIX),
                    (MigrationStage.DRIVER_IMPLEMENTATION, MigrationArtifact.IMPLEMENTATION_BUNDLE),
                    (MigrationStage.DRIVER_IMPLEMENTATION, MigrationArtifact.COMPLIANCE_REPORT),
                    (EnvironmentStage.RECOVERY, EnvironmentArtifact.MODE_RECORD),
                    (EnvironmentStage.RECOVERY, EnvironmentArtifact.EXPERIMENT_ROUTE),
                    (TargetStudyStage.STUDY, TargetStudyArtifact.STRUCTURED_PROFILE),
                ),
                extra={"controller_validation_error": preparation_error} if preparation_error else None,
            ),
            lambda p, job: self._accept_artifact_preparation_result(p, job, composite=bool(preparation_error)),
        )

    def _accept_artifact_preparation_result(
        self, project: Project, job: ArtifactOccurrence, *, composite: bool = False
    ) -> None:
        report = self._materialize_codex_report(project, MigrationStage.ARTIFACT_PREPARATION, job)
        from .migration.repair_execution import active, identity, record
        repair = composite or active(project, MigrationStage.ARTIFACT_PREPARATION)
        if repair:
            from .migration.review_policy import require_self_review
            require_self_review(report.read_text())
            try:
                identity(project)
            except WorkflowError as error:
                raise CodexOutputError(str(error)) from error
            record(project, report)
        try:
            ArtifactPreparationService().capture_codex_artifact(project, report)

        except ImplementationChanged as error:
            from .migration.review_policy import require_self_review
            require_self_review(report.read_text())
            # The same worker has already repaired and checked packaging-related
            # source changes. Rebind the implementation and invalidate dependent
            # receipts without another implementation AI turn.
            retry_prerequisite(project, MigrationStage.DRIVER_IMPLEMENTATION,
                trigger=MigrationStage.ARTIFACT_PREPARATION, reason=str(error))
            if project.stage(MigrationStage.DRIVER_IMPLEMENTATION).status is not StageStatus.READY:
                return
            project.start(MigrationStage.DRIVER_IMPLEMENTATION)
            if repair:
                record(project, report)
            DriverImplementationService().snapshot_worktree(project, report)
            project.start(MigrationStage.ARTIFACT_PREPARATION)
            ArtifactPreparationService().capture_codex_artifact(project, report)

    def _public_qemu(self, project: Project) -> None:
        from .migration.repair_execution import prepared
        prepared_report = prepared(project)
        execution = {}
        if prepared_report is not None:
            if project.stage(MigrationStage.PUBLIC_QEMU_VALIDATION).status is StageStatus.READY:
                project.start(MigrationStage.PUBLIC_QEMU_VALIDATION)
            request = project.artifacts.put_bytes(
                (f"Prepared repair: {prepared_report}\nDPF_RUN: PUBLIC_QEMU\n").encode(),
                kind=CodexArtifact.WORK_REPORT.value)
            acquisition = load_repository_acquisition(project)
            worktree = project.root / acquisition.target_worktree.path
            service = PublicQemuService()
            previous = service._latest_attempt(project)
            if previous is not None and previous[1].get("execution_status") != "PASS":
                result = {"status": "FAIL", "attempt": str(project.artifacts.path_for_digest(previous[0].digest))}
            else:
                try:
                    result = service.run_script(project,
                        script_path=worktree / ".dpf-output/public-qemu.sh",
                        work_report_path=project.artifacts.path_for_digest(request.digest))
                except CodexOutputError as error:
                    result = {"status": "FAIL", "error": str(error)}
            execution = {"controller_execution": result,
                         "repair_report": str(prepared_report)}
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
                    (MigrationStage.CONTRACTS, MigrationArtifact.TEST_PORT_MATRIX),
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
                    **execution,
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

    def _accept_public_qemu_result(self, project: Project, job: ArtifactOccurrence) -> None:
        report = self._materialize_codex_report(project, MigrationStage.PUBLIC_QEMU_VALIDATION, job)
        acquisition = load_repository_acquisition(project)
        worktree = (project.root / acquisition.target_worktree.path).resolve()
        try:
            service = PublicQemuService()
            from .orchestration.protocol import operation
            if operation(MigrationStage.PUBLIC_QEMU_VALIDATION.value, report.read_text()) == "PUBLIC_QEMU":
                result = service.run_script(
                    project, script_path=worktree / ".dpf-output" / "public-qemu.sh",
                    work_report_path=report,
                )
                raise CodexContinuation(
                    f"Controller execution completed: {result['attempt']}. Inspect the frozen "
                    "observations against the agreed oracles and complete the Skill's final "
                    "self-check in your existing report. Do not rerun an unchanged passing "
                    "suite. End with DPF_SELF_REVIEW: PASS only if requirements are met; "
                    "otherwise repair the cause or report a concrete blocker."
                )
            service.accept_self_review(project, work_report_path=report)
        except ImplementationChanged as error:
            retry_prerequisite(project, MigrationStage.DRIVER_IMPLEMENTATION,
                trigger=MigrationStage.PUBLIC_QEMU_VALIDATION, reason=str(error))

    def _public_repair(self, project: Project) -> None:
        try:
            decision = PublicRepairService.decision(project)
        except ImplementationChanged as error:
            if project.stage(MigrationStage.PUBLIC_REPAIR).status is StageStatus.READY:
                project.start(MigrationStage.PUBLIC_REPAIR)
            retry_prerequisite(project, MigrationStage.DRIVER_IMPLEMENTATION,
                trigger=MigrationStage.PUBLIC_REPAIR, reason=str(error))
            return
        if not decision["independent_required"]:
            PublicRepairService().finalize(project)
            return
        if PublicRepairService.reusable_review(project) is not None:
            PublicRepairService().finalize(project, reuse=True)
            return
        review_context = {"review_decision": decision}
        previous = [r for r in project.artifact_refs(stage=MigrationStage.PUBLIC_REPAIR)
                    if r.kind == CodexArtifact.WORK_REPORT.value]
        if previous:
            ref = max(previous, key=lambda r: r.ordinal)
            review_context["previous_review"] = {
                "path": str(project.artifacts.path_for_digest(ref.digest)),
                "digest": ref.digest, "kind": ref.kind,
                "status": "historical findings; compare with current repair evidence"}
        self._codex_gate(
            project, MigrationStage.PUBLIC_REPAIR,
            self._migration_context(project, (
                    (MigrationStage.HANDOFF, MigrationArtifact.HANDOFF),
                    (MigrationStage.DRIVER_IMPLEMENTATION, MigrationArtifact.IMPLEMENTATION_BUNDLE),
                    (MigrationStage.CONTRACTS, MigrationArtifact.CONTRACTS),
                    (MigrationStage.CONTRACTS, MigrationArtifact.TEST_PORT_MATRIX),
                    (MigrationStage.DRIVER_IMPLEMENTATION, MigrationArtifact.COMPLIANCE_REPORT),
                    (MigrationStage.ARTIFACT_PREPARATION, MigrationArtifact.ARTIFACT_IDENTITY),
                    (MigrationStage.PUBLIC_QEMU_VALIDATION, MigrationArtifact.PUBLIC_QEMU_REPORT),
                    (MigrationStage.PUBLIC_QEMU_VALIDATION, MigrationArtifact.PUBLIC_QEMU_WORK_REPORT),
            ), extra=review_context),
            self._accept_runtime_review,
        )

    def _accept_runtime_review(self, project: Project, job: ArtifactOccurrence) -> None:
        report = self._materialize_codex_report(project, MigrationStage.PUBLIC_REPAIR, job)
        lines = report.read_text().strip().splitlines()
        verdict = lines[-1] if lines else ""
        if verdict != "DPF_REVIEW: PASS":
            raise CodexOutputError("review must end with DPF_REVIEW: PASS or DPF_REVIEW: REWORK")
        PublicRepairService().finalize(project, review_path=report)

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
        from .migration.repair_execution import active
        if any(active(project, stage) for stage in (
                MigrationStage.DRIVER_IMPLEMENTATION, MigrationStage.ARTIFACT_PREPARATION)):
            context["frozen_inputs"][EnvironmentArtifact.MODE_RECORD.value] = self._artifact_context(
                project, EnvironmentStage.RECOVERY, EnvironmentArtifact.MODE_RECORD)
        # Every downstream migration task gets the current implementation identity,
        # not only a prose coverage report or a stale reference in session history.
        if project.stage(MigrationStage.DRIVER_IMPLEMENTATION).status is StageStatus.PASS:
            context["frozen_inputs"][MigrationArtifact.IMPLEMENTATION_BUNDLE.value] = self._artifact_context(
                project, MigrationStage.DRIVER_IMPLEMENTATION, MigrationArtifact.IMPLEMENTATION_BUNDLE)
        for stage, key in ((MigrationStage.PUBLIC_QEMU_VALIDATION, "runtime_work_report_path"),
                           (MigrationStage.PUBLIC_REPAIR, "runtime_review_path")):
            repair = project.retry_feedback(stage)
            if repair and repair["status"] == "RESOLVED":
                continue
            reports = [ref for ref in project.artifact_refs(stage=stage)
                       if ref.kind == CodexArtifact.WORK_REPORT.value]
            if reports:
                latest = max(reports, key=lambda ref: ref.ordinal or 0)
                context[key] = str(project.artifacts.path_for_digest(latest.digest))
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
