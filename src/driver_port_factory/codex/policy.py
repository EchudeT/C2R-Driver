from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..acquisition.repository import load_repository_acquisition
from ..core.contracts import StageKey
from ..core.models import WorkflowError
from ..core.project import Project
from ..migration.contracts import MigrationStage
from .contracts import CodexSandbox


@dataclass(frozen=True, slots=True)
class CodexExecutionGrant:
    execution_root: Path
    sandbox: CodexSandbox


class CodexExecutionPolicy:
    """Resolve the least-privilege filesystem grant owned by a workflow stage."""

    WRITABLE_STAGES = frozenset(
        {MigrationStage.DRIVER_IMPLEMENTATION, MigrationStage.PUBLIC_REPAIR}
    )

    def grant(self, project: Project, stage: StageKey) -> CodexExecutionGrant:
        if stage not in self.WRITABLE_STAGES:
            return CodexExecutionGrant(project.root, CodexSandbox.READ_ONLY)
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
        return CodexExecutionGrant(execution_root, CodexSandbox.WORKSPACE_WRITE)

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
