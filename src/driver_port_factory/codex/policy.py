from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..acquisition.contracts import AcquisitionStage
from ..acquisition.repository import load_repository_acquisition
from ..core.contracts import StageKey
from ..core.models import EvaluationMode, WorkflowError
from ..core.project import Project
from ..environment.contracts import EnvironmentStage
from ..migration.contracts import MigrationStage
from ..target_study.contracts import TargetStudyStage
from .contracts import CodexSandbox


@dataclass(frozen=True, slots=True)
class CodexExecutionGrant:
    execution_root: Path
    sandbox: CodexSandbox


class CodexExecutionPolicy:
    """Developer workers have full tool access; stage directories organize their work."""

    NETWORK_STAGES = frozenset(
        {AcquisitionStage.REPOSITORY_ACQUISITION, AcquisitionStage.EVIDENCE_CLOSURE}
    )
    WRITABLE_STAGES = frozenset(
        {
            MigrationStage.DRIVER_IMPLEMENTATION,
            MigrationStage.ARTIFACT_PREPARATION,
            MigrationStage.PUBLIC_QEMU_VALIDATION,
        }
    )
    DEPENDENCY_STAGES = WRITABLE_STAGES | frozenset(
        {EnvironmentStage.RECOVERY, MigrationStage.CONTRACTS}
    )
    REPORT_WORKSPACE_STAGES = frozenset(
        {
            EnvironmentStage.RECOVERY,
            TargetStudyStage.STUDY,
            MigrationStage.CONTRACTS,
            MigrationStage.PUBLIC_REPAIR,
            MigrationStage.ANALYSIS_REVIEW,
        }
    )

    def grant(self, project: Project, stage: StageKey) -> CodexExecutionGrant:
        developer = getattr(getattr(project, "config", None), "evaluation_mode", None) is EvaluationMode.DEVELOPER_EVIDENCE
        writable = CodexSandbox.UNRESTRICTED if developer else CodexSandbox.WORKSPACE_WRITE
        if stage in self.NETWORK_STAGES:
            return CodexExecutionGrant(project.root, CodexSandbox.UNRESTRICTED)
        if stage in self.REPORT_WORKSPACE_STAGES:
            execution_root = project.root / "work" / "stage-work" / stage.value
            execution_root.mkdir(parents=True, exist_ok=True)
            acquisition = load_repository_acquisition(project)
            frozen = tuple(
                self._project_path(project, record.checkout_path, f"{record.role.value} baseline")
                for record in acquisition.checkouts
            )
            self._validate_writable_root(project, execution_root, frozen)
            # Developer runs need to invoke the controller submission tool, which
            # writes an immutable receipt under the project control directory.
            # Keep the restricted workspace grant for evaluation workers, while
            # preserving the developer-mode unrestricted grant selected above.
            return CodexExecutionGrant(execution_root, writable)
        if stage not in self.WRITABLE_STAGES:
            return CodexExecutionGrant(project.root, CodexSandbox.UNRESTRICTED if developer else CodexSandbox.READ_ONLY)
        acquisition = load_repository_acquisition(project)
        execution_root = self._project_path(
            project,
            acquisition.target_worktree.path,
            "target worktree",
        )
        frozen = tuple(
            self._project_path(project, record.checkout_path, f"{record.role.value} baseline")
            for record in acquisition.checkouts
        )
        self._validate_writable_root(project, execution_root, frozen)
        return CodexExecutionGrant(execution_root, writable)

    @staticmethod
    def controlled_input(project: Project, value: str, label: str) -> Path:
        path = Path(value).resolve()
        if path != project.root and project.root not in path.parents:
            raise WorkflowError(f"{label} must be inside the current project workspace")
        if not path.is_file():
            raise WorkflowError(f"{label} does not exist: {path}")
        return path

    @staticmethod
    def _project_path(project: Project, value: object, label: str) -> Path:
        if not isinstance(value, str) or not value:
            raise WorkflowError(f"repository manifest has no {label} path")
        path = (project.root / value).resolve()
        if path != project.root and project.root not in path.parents:
            raise WorkflowError(f"{label} escapes the project workspace")
        if not path.is_dir():
            raise WorkflowError(f"{label} does not exist: {path}")
        return path

    @staticmethod
    def _validate_writable_root(
        project: Project,
        execution_root: Path,
        frozen_roots: tuple[Path, ...],
    ) -> None:
        control = project.control.resolve()
        if execution_root == project.root:
            raise WorkflowError("project root cannot be a Codex workspace-write grant")
        if execution_root == control or control in execution_root.parents:
            raise WorkflowError("control state cannot be a Codex workspace-write grant")
        if execution_root in control.parents:
            raise WorkflowError("Codex writable root cannot contain control state")
        if any(
            execution_root == frozen
            or execution_root in frozen.parents
            or frozen in execution_root.parents
            for frozen in frozen_roots
        ):
            raise WorkflowError("Codex writable root overlaps a frozen upstream checkout")
