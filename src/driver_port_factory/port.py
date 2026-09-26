from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .acquisition.closure import EvidenceClosureFinalizer
from .acquisition.contracts import AcquisitionArtifact, AcquisitionStage
from .acquisition.git_execution import RepositoryFetchError
from .acquisition.job import ArtifactOccurrence
from .acquisition.proposal import EvidenceProposalImporter
from .acquisition.repository import RepositoryAcquirer, load_repository_acquisition
from .acquisition.repository_role import RepositoryRole
from .acquisition.revision_proposal import RevisionProposalImporter
from .cli_support import CommandRegistry, command_registry
from .codex.cli import run_codex_stage
from .codex.contracts import (
    CodexArtifact,
    CodexBackend,
    CodexContinuation,
    CodexOutputError,
    ModelInvocationError,
)
from .composition import initialize_project, open_project
from .control.runtime import controller_run
from .core.checker_decision import CheckerDecisionRequired, RecoveryPaused
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
from .environment.contracts import EnvironmentArtifact, EnvironmentStage
from .environment.execution import ExperimentExecutor
from .environment.inventory import EnvironmentInspector
from .environment.models import ExperimentReadiness
from .intake.contracts import IntakeArtifact, IntakeStage
from .intake.service import IntakeService
from .knowledge.bootstrap import KnowledgeBootstrapper
from .knowledge.contracts import KnowledgeArtifact, KnowledgeStage
from .migration.artifact_preparation import ArtifactPreparationService
from .migration.contracts import MigrationArtifact, MigrationStage
from .migration.final_evidence_review import FinalEvidenceReviewService
from .migration.handoff import MigrationHandoff
from .migration.implementation import DriverImplementationService, ImplementationChanged
from .migration.public_qemu import PublicQemuService
from .migration.repair_routing import (
    ROUTES,
    PrerequisiteRepair,
    WorkerBlocked,
    retry_prerequisite,
)
from .migration.target_framework import TargetFrameworkEnablementService
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
    baseline_repositories: tuple[Path, ...] = ()
    local_source_repository: Path | None = None
    local_target_repository: Path | None = None
    local_qemu_repository: Path | None = None
    enable_analysis_review: bool | None = None
    enable_final_evidence_review: bool | None = None
    context_policy: str | None = None


@dataclass(frozen=True, slots=True)
class PortOutcome:
    stage: str
    status: StageStatus
    next_action: str
    report_path: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "stage": self.stage,
            "status": self.status.value,
            "next_action": self.next_action,
            "report_path": self.report_path,
        }


class PortRunner:
    """Run the worker and independent reviewer through functional delivery."""

    def __init__(self, options: PortOptions) -> None:
        self.options = options
        self._actions: dict[StageKey, Callable[[Project], None]] = {
            IntakeStage.REQUEST: self._intake,
            AcquisitionStage.REPOSITORY_ACQUISITION: self._repositories,
            AcquisitionStage.EVIDENCE_CLOSURE: self._evidence,
            EnvironmentStage.RECOVERY: self._environment,
            KnowledgeStage.KNOWLEDGE_BASE: self._knowledge,
            TargetStudyStage.STUDY: self._target_study,
            MigrationStage.HANDOFF: self._handoff,
            MigrationStage.CONTRACTS: self._contracts,
            MigrationStage.ANALYSIS_REVIEW: self._analysis_review,
            MigrationStage.TARGET_FRAMEWORK_ENABLEMENT: self._target_framework_enablement,
            MigrationStage.DRIVER_IMPLEMENTATION: self._implementation,
            MigrationStage.ARTIFACT_PREPARATION: self._artifact_preparation,
            MigrationStage.PUBLIC_QEMU_VALIDATION: self._public_qemu,
            MigrationStage.FINAL_EVIDENCE_REVIEW: self._final_evidence_review,
        }

    def run(self) -> PortOutcome:
        project = self._project()
        with controller_run(project):
            if self.options.context_policy is not None:
                from .codex.context_policy import configure_policy
                configure_policy(project, self.options.context_policy, reason="port run option")
            return self._run_project(project)

    def _run_project(self, project: Project) -> PortOutcome:
        while True:
            stage = self._current(project)
            if stage is None:
                final_stage = project.stages()[-1]
                report_path = None
                if MigrationStage.FINAL_EVIDENCE_REVIEW.value in project.workflow.stage_values:
                    review = project.artifact(
                        MigrationStage.FINAL_EVIDENCE_REVIEW, MigrationArtifact.FINAL_EVIDENCE_REVIEW_REPORT
                    )
                    report = json.loads(project.artifacts.read(review))["review"]
                    report_path = str(project.artifacts.path_for_digest(report["sha256"]))
                return PortOutcome(
                    final_stage.name.value,
                    StageStatus.PASS,
                    "complete",
                    report_path,
                )
            if stage.status is StageStatus.WAITING_FOR_USER:
                return PortOutcome(
                    stage.name.value, stage.status, "answer persisted intake question"
                )
            action = self._actions.get(stage.name)
            if action is None or stage.status not in {StageStatus.READY, StageStatus.RUNNING}:
                return PortOutcome(stage.name.value, stage.status, "resolve recorded blocker")
            try:
                from .core.checker_decision import pending_decision
                pending_check = pending_decision(project, stage.name)
                if pending_check is not None:
                    self._checker_decision(project, stage.name, pending_check)
                    continue
                action(project)
            except CheckerDecisionRequired:
                # Outputs and findings are already durable. Resume the same worker.
                continue
            except (ModelInvocationError, RepositoryFetchError, RecoveryPaused):
                raise
            except (WorkflowError, OSError, ValueError, subprocess.SubprocessError) as error:
                if project.config.evaluation_mode is not EvaluationMode.DEVELOPER_EVIDENCE:
                    raise
                # Recovery is only safe with an intact ledger. Never ask the worker
                # to patch immutable evidence or conceal corrupt persisted state.
                project.verify_integrity()
                from .core.checker_decision import request_recovery
                try:
                    request_recovery(project, stage.name, error)
                except CheckerDecisionRequired:
                    pass
                continue
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
            if self.options.baseline_repositories and tuple(str(path.resolve()) for path in
                    self.options.baseline_repositories) != project.config.baseline_repositories:
                raise WorkflowError("supplied baseline repositories differ from the persisted run configuration")
            for option_name, option_value, config_value in (
                (
                    "local_source_repository",
                    str(self.options.local_source_repository.resolve())
                    if self.options.local_source_repository else None,
                    project.config.local_source_repository,
                ),
                (
                    "local_target_repository",
                    str(self.options.local_target_repository.resolve())
                    if self.options.local_target_repository else None,
                    project.config.local_target_repository,
                ),
                (
                    "local_qemu_repository",
                    str(self.options.local_qemu_repository.resolve())
                    if self.options.local_qemu_repository else None,
                    project.config.local_qemu_repository,
                ),
                ("analysis_review", self.options.enable_analysis_review,
                 project.config.enable_analysis_review),
                ("final_evidence_review", self.options.enable_final_evidence_review,
                 project.config.enable_final_evidence_review),
            ):
                if option_value is not None and option_value != config_value:
                    raise WorkflowError(
                        f"supplied {option_name} setting differs from the persisted "
                        "run configuration"
                    )
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
                baseline_repositories=tuple(
                    str(path.resolve()) for path in self.options.baseline_repositories
                ),
                local_source_repository=(
                    str(self.options.local_source_repository.resolve())
                    if self.options.local_source_repository else None
                ),
                local_target_repository=(
                    str(self.options.local_target_repository.resolve())
                    if self.options.local_target_repository else None
                ),
                local_qemu_repository=(
                    str(self.options.local_qemu_repository.resolve())
                    if self.options.local_qemu_repository else None
                ),
                enable_analysis_review=(
                    True if self.options.enable_analysis_review is None
                    else self.options.enable_analysis_review
                ),
                enable_final_evidence_review=(
                    True if self.options.enable_final_evidence_review is None
                    else self.options.enable_final_evidence_review
                ),
            ),
        )

    def _checker_decision(self, project: Project, stage: StageKey, error) -> None:
        from .core.checker_decision import accept_decision, capture_is_current, clear_pending
        from .core.models import StageOwner
        payload = json.loads(error.path.read_text())
        job = self._latest_job_occurrence(project, stage)
        if job is None or job.ordinal <= payload["after_job"]:
            _, _, response = self._codex(
                project, stage,
                {"checker_decision": True, "checker_findings": str(error),
                 "captured_outputs": str(error.path)},
            )
            job = self._job_occurrence(project, stage, response)
        submission = self._submission_for_job(project, stage, job)
        try:
            if submission is None:
                raise CodexOutputError(
                    "checker response must be submitted as a report through the submission tool"
                )
            if submission.get("kind") == "report":
                report = self._materialize_codex_report(project, stage, job)
            elif submission.get("kind") == "proposal":
                # A recovery worker may have repaired the captured proposal
                # and resubmitted it through the stage's normal proposal
                # interface.  Re-enter the ordinary adapter so it can import
                # and validate that proposal; do not treat it as a checker
                # acceptance report.
                report = None
            else:
                raise CodexOutputError(
                    "checker response must be submitted as a report or repaired proposal"
                )
        except WorkerBlocked as blocker:
            clear_pending(project, stage)
            project.complete(stage, StageStatus.BLOCKED, message=str(blocker))
            return
        except PrerequisiteRepair:
            if project.stage(stage).owner is StageOwner.STATIC:
                raise
            # Let the normal model-stage adapter apply the same routing and
            # sealed-phase rules as it does for any other deliverable.
            report = None
        if (report is not None
                and submission is not None
                and submission.get("decision") == "pass"
                and capture_is_current(project, stage, payload)):
            accept_decision(project, stage, error.path, report)
        else:
            # Missing/stale captures must go through the normal adapter, even if
            # the worker calls its response ACCEPT. Reuse the persisted response;
            # never ask another model turn to correct a routing-only distinction.
            clear_pending(project, stage)
            self._actions[stage](project)

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
        while True:
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
                submission = self._submission_for_job(project, stage, pending)
                if submission is not None:
                    decision = submission.get("decision")
                    if decision == "blocked":
                        report = str(submission.get("file"))
                        detail = self._blocked_report_detail(report)
                        raise WorkerBlocked(
                            "worker reported a prerequisite blocker; "
                            f"report={report}; findings={detail}"
                        )
                    if decision == "rework":
                        target_name = submission.get("repair_stage")
                        target = ROUTES.get(target_name)
                        if target is None:
                            raise CodexOutputError(
                                "submitted rework decision names no valid repair stage"
                            )
                        raise PrerequisiteRepair(
                            target, str(submission.get("file"))
                        )
                accept(project, pending)
                return True
            except WorkerBlocked as error:
                project.complete(stage, StageStatus.BLOCKED, message=str(error))
                return False
            except PrerequisiteRepair as error:
                target = project.stage(error.target)
                if (target.status is not StageStatus.PASS or target.position >= project.stage(stage).position
                        or stage.value not in project.workflow.descendants(error.target)):
                    raise CodexOutputError(
                        "Repair locally; --repair-stage must name a passed actual data "
                        "prerequisite in instructions.repair_targets within the current phase."
                    ) from error
                retry_prerequisite(project, error.target, trigger=stage, reason=str(error))
                return False
            except CodexContinuation as progress:
                context = self._continuation_context(project, stage, pending, progress, context)
                if context is None:
                    return False
                feedback = None
                pending = None

    def _continuation_context(self, project, stage, pending, progress, context):
        """Observe one failed submission, or hand successful execution to self-review."""
        from .core.continuation import record_continuation
        if not progress.counts_as_failure:
            # Public operation requests already have a durable operation guard.
            return {**context, "controller_execution": str(progress)}
        fingerprint = self._continuation_fingerprint(project, stage, progress)
        history = record_continuation(
            project, stage, pending, fingerprint,
            detail=self._continuation_detail(progress), receipt=progress.receipt,
        )
        if history["consecutive"] >= 3:
            message = (
                "bounded repair paused after three identical continuation observations; "
                "no implementation, runtime, harness or evidence input changed. "
                f"stage={stage.value}; "
                f"last_receipt={progress.receipt or self._latest_receipt(project, stage)}; "
                f"finding={self._continuation_detail(progress)}; fingerprint={fingerprint}. "
                "Apply the cited repair or submit an explicit prerequisite rework after "
                "changing the affected input, then reopen this stage."
            )
            project.note_check(message)
            project.complete(stage, StageStatus.BLOCKED, message=message)
            return None
        return {**context, "controller_execution": str(progress), "repair_observation": {
            "consecutive": history["consecutive"], "receipt": progress.receipt,
            "observed": progress.observation,
            "next": "Inspect the causal failure and new evidence. Collector success does not "
            "establish driver behavior. If the premise is wrong, use the existing "
            "checker-decision or prerequisite rework route.",
        }}

    @staticmethod
    def _latest_receipt(project: Project, stage: StageKey) -> str:
        refs = [
            ref for ref in project.artifact_refs(stage=stage)
            if ref.kind == CodexArtifact.SUBMISSION.value and ref.ordinal is not None
        ]
        if not refs:
            return "none"
        ref = max(refs, key=lambda item: item.ordinal)
        try:
            value = json.loads(project.artifacts.read(ref))
            return str(value.get("file") or project.artifacts.path_for_digest(ref.digest))
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError):
            return str(project.artifacts.path_for_digest(ref.digest))

    @staticmethod
    def _blocked_report_detail(path: str) -> str:
        """Keep the durable stage message useful without copying a whole report."""
        try:
            text = Path(path).read_text(encoding="utf-8").strip()
        except (OSError, UnicodeError):
            return "unable to read submitted blocker report"
        if not text:
            return "submitted blocker report is blank"
        # Preserve the beginning because reports conventionally put the
        # classification, exact locations and required action first. The full
        # report remains immutable in CAS at the path shown above.
        return " ".join(text[:2400].split())

    @staticmethod
    def _continuation_fingerprint(project: Project, stage: StageKey, error: Exception) -> str:
        """Hash executable premises, not report prose or attempt UUIDs.

        Continuation text and receipt directories change on every attempt.  They
        must not trick the controller into paying for an identical repair.
        """
        normalized = PortRunner._continuation_detail(error)
        value: dict[str, object] = {"stage": stage.value, "error": normalized, "artifacts": []}
        value["observation"] = getattr(error, "observation", {})
        try:
            refs = [
                ref for ref in project.current_artifact_refs(stage=stage)
                if ref.kind not in {
                    CodexArtifact.JOB_RESULT.value,
                    CodexArtifact.WORK_REPORT.value,
                    CodexArtifact.SUBMISSION.value,
                    CodexArtifact.PROMPT.value,
                    CodexArtifact.EVENT_LOG.value,
                }
            ]
            value["artifacts"] = [(ref.kind, ref.digest) for ref in refs]
            acquisition = load_repository_acquisition(project)
            target = acquisition.target_worktree
            worktree = project.root / target.path
            from .migration.implementation import worktree_files
            value["worktree"] = worktree_files(worktree, target.base_commit)
            from .migration.public_qemu import PublicQemuService
            value["helpers"] = PublicQemuService._helper_inputs(worktree)
            output = worktree / ".dpf-output"
            for name in (
                "runtime-artifact",
                "check-presence.sh",
                "implementation-smoke.sh",
                "public-qemu.sh",
            ):
                path = output / name
                if path.is_file() and not path.is_symlink():
                    value.setdefault("execution", []).append(
                        (name, hashlib.sha256(path.read_bytes()).hexdigest())
                    )
        except (OSError, WorkflowError, ValueError):
            # A missing checkout is itself a stable failure premise.  Preserve
            # the normalized error so the guard still prevents a hot loop.
            pass
        encoded = json.dumps(value, sort_keys=True, default=str).encode()
        return hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def _continuation_detail(error: Exception) -> str:
        """Retain the exact finding while removing attempt-specific noise."""
        normalized = str(error)
        normalized = re.sub(
            r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
            "<attempt>", normalized, flags=re.IGNORECASE,
        )
        normalized = re.sub(
            r"/(?:implementation-smoke|artifact-preparation|public-qemu)/[0-9a-f]{32}",
            lambda match: match.group(0).rsplit("/", 1)[0] + "/<attempt>",
            normalized, flags=re.IGNORECASE,
        )
        return " ".join(normalized[:2400].split())

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
            return path
        data = project.artifacts.read(matches[0])
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as error:
            raise CodexOutputError("Codex work report is not UTF-8") from error
        if not text.strip():
            raise CodexOutputError("Codex work report is blank; write the report and submit it with the tool")
        submission = PortRunner._submission_for_job(project, stage, job)
        if submission is not None and submission.get("kind") == "report":
            report_path = Path(str(submission["file"])).resolve()
            report_data = report_path.read_bytes()
            report_text = report_data.decode("utf-8")
            report_data = report_text.encode("utf-8")
            frozen = project.record_artifact(
                stage, GeneratedArtifact(CodexArtifact.WORK_REPORT, report_data, source)
            )
            path = project.artifacts.path_for_digest(frozen.digest)
            if submission.get("decision") == "blocked":
                raise WorkerBlocked(f"worker reported a blocker; submitted file: {submission.get('file')}")
            if submission.get("decision") == "rework":
                target = ROUTES.get(submission.get("repair_stage"))
                if target is None:
                    raise CodexOutputError("submitted rework decision names no valid repair stage")
                raise PrerequisiteRepair(target, str(submission.get("file")))
            return path
        raise CodexOutputError(
            "Codex report has no validated submission receipt; submit the report through the tool"
        )

    @staticmethod
    def _submission_for_job(project: Project, stage: StageKey, job: ArtifactOccurrence):
        matches = [
            ref for ref in project.current_artifact_refs(stage=stage)
            if ref.kind == CodexArtifact.SUBMISSION.value
        ]
        candidates = []
        for ref in matches:
            try:
                value = json.loads(project.artifacts.read(ref))
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            if (value.get("job_result_digest") == job.digest
                    and value.get("job_result_ordinal") == job.ordinal):
                candidates.append(value)
        if len(candidates) > 1:
            raise CodexOutputError("multiple submissions are bound to one Codex result")
        return candidates[0] if candidates else None

    def _intake(self, project: Project) -> None:
        IntakeService().analyze(
            project,
            raw_request=(
                f"Port {self.options.driver_name} from {self.options.source_platform} "
                f"to {self.options.target_platform} in Rust and validate it with QEMU."
            ),
            catalog_paths=self.options.catalogs,
        )

    def _repositories(self, project: Project) -> None:
        envelope = self._artifact_context(
            project,
            IntakeStage.ENVELOPE_FREEZE,
            IntakeArtifact.MIGRATION_ENVELOPE,
        )
        self._codex_gate(
            project,
            AcquisitionStage.REPOSITORY_ACQUISITION,
            {
                "migration_envelope": envelope,
                "supplied_upstream_baselines": list(project.config.baseline_repositories),
                "supplied_local_repositories": {
                    role.value: path
                    for role, path in (
                        (RepositoryRole.SOURCE, project.config.local_source_repository),
                        (RepositoryRole.TARGET, project.config.local_target_repository),
                        (RepositoryRole.QEMU, project.config.local_qemu_repository),
                    )
                    if path
                },
            },
            self._accept_revision_result,
        )

    @staticmethod
    def _accept_revision_result(project: Project, job: ArtifactOccurrence) -> None:
        try:
            proposal = RevisionProposalImporter().import_job_result(
                project, job_digest=job.digest, job_ordinal=job.ordinal
            )
            RepositoryAcquirer().acquire(project, proposal=proposal)
        except (CodexOutputError, CheckerDecisionRequired, RepositoryFetchError):
            raise
        except WorkflowError as error:
            raise CodexOutputError(f"revision proposal failed: {error}") from error

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
        review = self._previous_review(project, MigrationStage.ANALYSIS_REVIEW)
        if review:
            context["analysis_review"] = review
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

    def _contracts(self, project: Project) -> None:
        self._codex_gate(
            project,
            MigrationStage.CONTRACTS,
            self._migration_context(
                project,
                (
                    (MigrationStage.HANDOFF, MigrationArtifact.HANDOFF),
                    (AcquisitionStage.EVIDENCE_CLOSURE, AcquisitionArtifact.MATERIALS_MANIFEST),
                    (AcquisitionStage.EVIDENCE_CLOSURE, AcquisitionArtifact.EVIDENCE_GAP_REGISTER),
                    (KnowledgeStage.KNOWLEDGE_BASE, KnowledgeArtifact.QUERY_CONTRACT),
                    (TargetStudyStage.STUDY, TargetStudyArtifact.REPORT),
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

    @staticmethod
    def _previous_review(project: Project, stage: StageKey):
        if stage.value not in project.workflow.stage_values:
            return None
        all_refs = project.artifact_refs(stage=stage)
        refs = [ref for ref in all_refs if ref.kind == CodexArtifact.WORK_REPORT.value]
        if not refs:
            # A rejected review may be outside the current artifact boundary.
            # Recover its immutable job result through the bound submission
            # receipt so the next reviewer sees the prior findings.
            jobs = {
                ref.ordinal: ref for ref in all_refs
                if ref.kind == CodexArtifact.JOB_RESULT.value and ref.ordinal is not None
            }
            rejected = []
            for receipt in all_refs:
                if receipt.kind != CodexArtifact.SUBMISSION.value:
                    continue
                try:
                    value = json.loads(project.artifacts.read(receipt))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    continue
                if value.get("decision") == "rework" and value.get("job_result_ordinal") in jobs:
                    rejected.append(jobs[value["job_result_ordinal"]])
            refs = rejected or [ref for ref in all_refs
                                if ref.kind == CodexArtifact.JOB_RESULT.value]
        if not refs:
            return None
        ref = max(refs, key=lambda item: item.ordinal)
        return {"path": str(project.artifacts.path_for_digest(ref.digest)),
                "digest": ref.digest, "kind": ref.kind,
                "status": "historical findings; compare with current inputs and repair evidence"}

    @staticmethod
    def _review_job_policy(project: Project, stage: StageKey, job: ArtifactOccurrence):
        occurrence = next(ref for ref in project.current_artifact_refs(stage=stage)
                          if ref.kind == CodexArtifact.JOB_RESULT.value and ref.ordinal == job.ordinal)
        metrics = json.loads(Path(occurrence.source).with_suffix(".metrics.json").read_text())
        policy = metrics.get("policy_sha256")
        if not policy:
            raise CodexOutputError("Review has no recorded rule identity; review current rules before acceptance")
        return policy

    def _analysis_review(self, project: Project) -> None:
        from .migration.analysis_review import INPUTS, AnalysisReviewService
        service = AnalysisReviewService()
        reused = service.reusable(project, self.options.skill_root)
        if reused is not None:
            service.finalize(project, text=reused,
                policy_digest=service.policy(project, self.options.skill_root),
                skill_root=self.options.skill_root)
            return
        self._codex_gate(project, MigrationStage.ANALYSIS_REVIEW,
            self._migration_context(project, INPUTS, extra={
                "previous_review": self._previous_review(project, MigrationStage.ANALYSIS_REVIEW)}),
            self._accept_analysis_review)

    def _accept_analysis_review(self, project: Project, job: ArtifactOccurrence) -> None:
        from .migration.analysis_review import AnalysisReviewService
        report = self._materialize_codex_report(project, MigrationStage.ANALYSIS_REVIEW, job)
        AnalysisReviewService.finalize(project, text=report.read_text(),
            policy_digest=self._review_job_policy(project, MigrationStage.ANALYSIS_REVIEW, job),
            skill_root=self.options.skill_root)

    def _target_framework_enablement(self, project: Project) -> None:
        self._codex_gate(
            project,
            MigrationStage.TARGET_FRAMEWORK_ENABLEMENT,
            self._migration_context(
                project,
                (
                    (MigrationStage.HANDOFF, MigrationArtifact.HANDOFF),
                    (MigrationStage.CONTRACTS, MigrationArtifact.CONTRACTS),
                    (MigrationStage.CONTRACTS, MigrationArtifact.TEST_PORT_MATRIX),
                    (TargetStudyStage.STUDY, TargetStudyArtifact.REPORT),
                    (KnowledgeStage.KNOWLEDGE_BASE, KnowledgeArtifact.QUERY_CONTRACT),
                ),
            ),
            self._accept_target_framework_enablement,
        )

    def _accept_target_framework_enablement(
        self, project: Project, job: ArtifactOccurrence
    ) -> None:
        report = self._materialize_codex_report(
            project, MigrationStage.TARGET_FRAMEWORK_ENABLEMENT, job
        )
        TargetFrameworkEnablementService().snapshot_worktree(project, report)

    def _implementation(self, project: Project) -> None:
        from .migration.repair_execution import active, prepared
        report = prepared(project) if active(project, MigrationStage.DRIVER_IMPLEMENTATION) else None
        if report is not None:
            if project.stage(MigrationStage.DRIVER_IMPLEMENTATION).status is StageStatus.READY:
                project.start(MigrationStage.DRIVER_IMPLEMENTATION)
            try:
                DriverImplementationService().snapshot_worktree(project, report)
                return
            except CodexContinuation:
                # A prepared repair still needs the current functional self-test.
                # Resume the implementation worker to create/fix its harness.
                pass
        self._codex_gate(
            project,
            MigrationStage.DRIVER_IMPLEMENTATION,
            self._migration_context(
                project,
                (
                    (MigrationStage.HANDOFF, MigrationArtifact.HANDOFF),
                    (MigrationStage.CONTRACTS, MigrationArtifact.CONTRACTS),
                    (MigrationStage.CONTRACTS, MigrationArtifact.TEST_PORT_MATRIX),
                    (MigrationStage.TARGET_FRAMEWORK_ENABLEMENT,
                     MigrationArtifact.TARGET_FRAMEWORK_BUNDLE),
                    (KnowledgeStage.KNOWLEDGE_BASE, KnowledgeArtifact.QUERY_CONTRACT),
                    (KnowledgeStage.KNOWLEDGE_BASE, KnowledgeArtifact.GENERATED_SKILL),
                    (TargetStudyStage.STUDY, TargetStudyArtifact.REPORT),
                ),
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
            except ImplementationChanged as error:
                retry_prerequisite(
                    project,
                    MigrationStage.DRIVER_IMPLEMENTATION,
                    trigger=MigrationStage.ARTIFACT_PREPARATION,
                    reason=str(error),
                )
                return
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
                    (MigrationStage.TARGET_FRAMEWORK_ENABLEMENT,
                     MigrationArtifact.TARGET_FRAMEWORK_BUNDLE),
                    (MigrationStage.TARGET_FRAMEWORK_ENABLEMENT,
                     MigrationArtifact.TARGET_FRAMEWORK_CHANGE_INVENTORY),
                    (EnvironmentStage.RECOVERY, EnvironmentArtifact.MODE_RECORD),
                    (EnvironmentStage.RECOVERY, EnvironmentArtifact.EXPERIMENT_ROUTE),
                    (TargetStudyStage.STUDY, TargetStudyArtifact.REPORT),
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
                (f"Prepared repair: {prepared_report}\n").encode(),
                kind=CodexArtifact.WORK_REPORT.value)
            acquisition = load_repository_acquisition(project)
            worktree = project.root / acquisition.target_worktree.path
            service = PublicQemuService()
            try:
                result = service.run_script(project,
                    script_path=worktree / ".dpf-output/public-qemu.sh",
                    work_report_path=project.artifacts.path_for_digest(request.digest))
            except ImplementationChanged as error:
                retry_prerequisite(
                    project,
                    MigrationStage.DRIVER_IMPLEMENTATION,
                    trigger=MigrationStage.PUBLIC_QEMU_VALIDATION,
                    reason=str(error),
                )
                return
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
                        MigrationArtifact.COMPLIANCE_REPORT,
                    ),
                    (MigrationStage.ARTIFACT_PREPARATION, MigrationArtifact.RUNTIME_ARTIFACT),
                    (MigrationStage.ARTIFACT_PREPARATION, MigrationArtifact.ARTIFACT_IDENTITY),
                    (EnvironmentStage.RECOVERY, EnvironmentArtifact.EXPERIMENT_ROUTE),
                    (KnowledgeStage.KNOWLEDGE_BASE, KnowledgeArtifact.QUERY_CONTRACT),
                    (AcquisitionStage.EVIDENCE_CLOSURE, AcquisitionArtifact.MATERIALS_MANIFEST),
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
            submission = self._submission_for_job(project, MigrationStage.PUBLIC_QEMU_VALIDATION, job)
            if (submission and submission.get("decision") == "operation"
                    and submission.get("operation") == "PUBLIC_QEMU"):
                from .core.checker_decision import guard_operation
                guard_operation(project, MigrationStage.PUBLIC_QEMU_VALIDATION, job.ordinal)
                result = service.run_script(
                    project, script_path=worktree / ".dpf-output" / "public-qemu.sh",
                    work_report_path=report,
                )
                raise CodexContinuation(
                    f"Controller execution completed: {result['attempt']}. Inspect the frozen "
                    "observations against the agreed oracles and complete the Skill's final "
                    "self-check in your existing report. Do not rerun an unchanged passing "
                    "suite. Submit pass only if requirements are met; otherwise repair the "
                    "cause or submit a concrete blocker.", counts_as_failure=False,
                )
            service.accept_self_review(project, work_report_path=report)
        except ImplementationChanged as error:
            retry_prerequisite(project, MigrationStage.DRIVER_IMPLEMENTATION,
                trigger=MigrationStage.PUBLIC_QEMU_VALIDATION, reason=str(error))

    def _final_evidence_review(self, project: Project) -> None:
        if FinalEvidenceReviewService.reusable_review(project, skill_root=self.options.skill_root) is not None:
            FinalEvidenceReviewService().finalize(project, reuse=True, skill_root=self.options.skill_root)
            return
        review_context = {}
        previous = [r for r in project.artifact_refs(stage=MigrationStage.FINAL_EVIDENCE_REVIEW)
                    if r.kind == CodexArtifact.WORK_REPORT.value]
        if not previous:
            previous = [r for r in project.artifact_refs(stage=MigrationStage.FINAL_EVIDENCE_REVIEW)
                        if r.kind == CodexArtifact.JOB_RESULT.value]
        if previous:
            ref = max(previous, key=lambda r: r.ordinal)
            review_context["previous_review"] = {
                "path": str(project.artifacts.path_for_digest(ref.digest)),
                "digest": ref.digest, "kind": ref.kind,
                "status": "historical findings; compare with current repair evidence"}
        self._codex_gate(
            project, MigrationStage.FINAL_EVIDENCE_REVIEW,
            self._migration_context(project, (
                    (MigrationStage.TARGET_FRAMEWORK_ENABLEMENT,
                     MigrationArtifact.TARGET_FRAMEWORK_BUNDLE),
                    (MigrationStage.TARGET_FRAMEWORK_ENABLEMENT,
                     MigrationArtifact.TARGET_FRAMEWORK_REPORT),
                    (MigrationStage.TARGET_FRAMEWORK_ENABLEMENT,
                     MigrationArtifact.TARGET_FRAMEWORK_CHANGE_INVENTORY),
                    (MigrationStage.DRIVER_IMPLEMENTATION, MigrationArtifact.TARGET_CHANGE_INVENTORY),
                    (MigrationStage.DRIVER_IMPLEMENTATION, MigrationArtifact.COMPLIANCE_REPORT),
                    (MigrationStage.ARTIFACT_PREPARATION, MigrationArtifact.ARTIFACT_IDENTITY),
                    (MigrationStage.PUBLIC_QEMU_VALIDATION, MigrationArtifact.PUBLIC_QEMU_REPORT),
                    (MigrationStage.PUBLIC_QEMU_VALIDATION, MigrationArtifact.PUBLIC_QEMU_WORK_REPORT),
            ), extra={
                **review_context,
                "implementation_snapshot": self._implementation_review_context(project),
                "frozen_acceptance_oracles": {
                    "contracts": self._artifact_digest_context(
                        project, MigrationStage.CONTRACTS, MigrationArtifact.CONTRACTS
                    ),
                    "test_matrix": self._artifact_digest_context(
                        project, MigrationStage.CONTRACTS, MigrationArtifact.TEST_PORT_MATRIX
                    ),
                },
            }, include_implementation_bundle=False, include_review_history=False,
            include_knowledge_skill=False),
            self._accept_runtime_review,
        )

    def _accept_runtime_review(self, project: Project, job: ArtifactOccurrence) -> None:
        report = self._materialize_codex_report(project, MigrationStage.FINAL_EVIDENCE_REVIEW, job)
        submission = self._submission_for_job(project, MigrationStage.FINAL_EVIDENCE_REVIEW, job)
        if not submission or submission.get("decision") != "pass":
            raise CodexOutputError("independent review must be submitted with the pass decision")
        policy = self._review_job_policy(project, MigrationStage.FINAL_EVIDENCE_REVIEW, job)
        FinalEvidenceReviewService().finalize(project, review_path=report, policy_digest=policy,
                                      skill_root=self.options.skill_root)

    def _migration_context(
        self,
        project: Project,
        inputs: tuple[tuple[StageKey, ArtifactKey], ...],
        *,
        extra: dict[str, object] | None = None,
        include_implementation_bundle: bool = True,
        include_review_history: bool = True,
        include_knowledge_skill: bool = True,
    ) -> dict[str, object]:
        acquisition = load_repository_acquisition(project)
        context: dict[str, object] = {
            "workspace_paths": {
                "project_root": str(project.root),
                "target_worktree": str(project.root / acquisition.target_worktree.path),
                "frozen_baselines": {
                    record.role.value: {"path": str(project.root / record.checkout_path),
                                        "revision": record.resolved_commit}
                    for record in acquisition.checkouts
                },
            },
            "frozen_inputs": {
                kind.value: self._artifact_context(project, owner, kind) for owner, kind in inputs
            }
        }
        if include_knowledge_skill:
            context["workspace_paths"]["knowledge_skill"] = self._artifact_context(
                project, KnowledgeStage.KNOWLEDGE_BASE, KnowledgeArtifact.GENERATED_SKILL
            )["path"]
        context.update(extra or {})
        from .migration.repair_execution import active
        if any(active(project, stage) for stage in (
                MigrationStage.DRIVER_IMPLEMENTATION, MigrationStage.ARTIFACT_PREPARATION)):
            context["frozen_inputs"][EnvironmentArtifact.MODE_RECORD.value] = self._artifact_context(
                project, EnvironmentStage.RECOVERY, EnvironmentArtifact.MODE_RECORD)
        # Every downstream migration task gets the current implementation identity,
        # not only a prose coverage report or a stale reference in session history.
        if (include_implementation_bundle
                and project.stage(MigrationStage.DRIVER_IMPLEMENTATION).status is StageStatus.PASS):
            context["frozen_inputs"][MigrationArtifact.IMPLEMENTATION_BUNDLE.value] = self._artifact_context(
                project, MigrationStage.DRIVER_IMPLEMENTATION, MigrationArtifact.IMPLEMENTATION_BUNDLE)
        if not include_review_history:
            return context
        for stage, key in ((MigrationStage.ANALYSIS_REVIEW, "analysis_review_path"),
                           (MigrationStage.PUBLIC_QEMU_VALIDATION, "runtime_work_report_path"),
                           (MigrationStage.FINAL_EVIDENCE_REVIEW, "runtime_review_path")):
            if stage.value not in project.workflow.stage_values:
                continue
            repair = project.retry_feedback(stage)
            if repair and repair["status"] == "RESOLVED":
                continue
            reports = [ref for ref in project.artifact_refs(stage=stage)
                       if ref.kind == CodexArtifact.WORK_REPORT.value]
            if reports:
                latest = max(reports, key=lambda ref: ref.ordinal or 0)
                context[key] = str(project.artifacts.path_for_digest(latest.digest))
        return context

    @staticmethod
    def _artifact_digest_context(
        project: Project, stage: StageKey, kind: ArtifactKey,
    ) -> dict[str, object]:
        reference = project.artifact(stage, kind)
        return {"kind": reference.kind, "digest": reference.digest, "size_bytes": reference.size}

    @staticmethod
    def _implementation_review_context(project: Project) -> dict[str, object]:
        bundle = project.load_json_artifact(
            MigrationStage.DRIVER_IMPLEMENTATION, MigrationArtifact.IMPLEMENTATION_BUNDLE
        )
        reference = project.artifact(
            MigrationStage.DRIVER_IMPLEMENTATION, MigrationArtifact.IMPLEMENTATION_BUNDLE
        )
        return {
            "bundle_digest": reference.digest,
            "target_worktree": bundle["target_worktree"],
            "files": bundle["files"],
            "work_report": bundle["work_report"],
        }


def _default_skill_root() -> Path:
    """Return the repository-local upstream Skill mirror by default.

    A caller can still select another frozen Skill tree with ``--skill-root``
    or ``DPF_SKILL_ROOT``.  The local mirror is preferred so a normal run is
    self-contained and does not depend on a separate checkout under
    ``CODEX_HOME``.
    """

    configured = os.environ.get("DPF_SKILL_ROOT")
    if configured:
        return Path(configured).expanduser().resolve()
    repository_root = Path(__file__).resolve().parents[2]
    local = repository_root / "skill"
    if local.is_dir():
        return local
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
            baseline_repositories=tuple(Path(path).resolve() for path in arguments.baseline_repository),
            local_source_repository=(
                Path(arguments.local_source_repository).resolve()
                if arguments.local_source_repository else None
            ),
            local_target_repository=(
                Path(arguments.local_target_repository).resolve()
                if arguments.local_target_repository else None
            ),
            local_qemu_repository=(
                Path(arguments.local_qemu_repository).resolve()
                if arguments.local_qemu_repository else None
            ),
            enable_analysis_review=arguments.analysis_review,
            enable_final_evidence_review=arguments.final_evidence_review,
            context_policy=arguments.context_policy,
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
    run.add_argument("--baseline-repository", action="append", default=[],
                     help="read-only upstream checkout/bare cache to reuse (repeatable; frozen on run creation)")
    run.add_argument(
        "--local-source-repository",
        help="existing local Linux/source checkout; import the selected commit without network",
    )
    run.add_argument(
        "--local-target-repository",
        help="existing local Asterinas/target checkout; import the selected commit without network",
    )
    run.add_argument(
        "--local-qemu-repository",
        help="existing local QEMU checkout or bare repository; import the selected commit without network",
    )
    run.add_argument(
        "--backend", type=CodexBackend, choices=list(CodexBackend), default=CodexBackend.EXEC
    )
    run.add_argument("--codex-bin", default="codex")
    run.add_argument("--model")
    from .codex.context_policy import POLICIES
    run.add_argument("--context-policy", choices=POLICIES,
                     help="persist optional context strategy; omitted keeps the current setting")
    run.add_argument(
        "--analysis-review", action=argparse.BooleanOptionalAction, default=None,
        help="enable or disable stage 13 analysis review (default: enabled)",
    )
    run.add_argument(
        "--final-evidence-review", action=argparse.BooleanOptionalAction, default=None,
        help="enable or disable stage 18 final evidence review (default: enabled; required for blind mode)",
    )
    run.set_defaults(handler=command_port_run)
