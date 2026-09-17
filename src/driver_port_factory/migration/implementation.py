from __future__ import annotations

import json
import subprocess
from pathlib import Path, PurePosixPath
from typing import Any

from ..acquisition.repository import load_repository_acquisition
from ..core.models import FileArtifact, GeneratedArtifact, StageStatus, WorkflowError
from ..core.project import Project
from ..core.validation import BundleValidationContext, json_object
from ..knowledge.contracts import KnowledgeArtifact
from ..knowledge.index import file_sha256
from ..source_analysis.contracts import SourceAnalysisArtifact
from ..target_study.contracts import TargetStudyArtifact
from .contracts import MigrationArtifact, MigrationStage

IMPLEMENTATION_INPUTS = (
    MigrationArtifact.HANDOFF,
    MigrationArtifact.CONTRACTS,
    MigrationArtifact.TEST_PORT_MATRIX,
    KnowledgeArtifact.QUERY_CONTRACT,
    KnowledgeArtifact.GENERATED_SKILL,
    TargetStudyArtifact.STRUCTURED_PROFILE,
    TargetStudyArtifact.API_EVIDENCE,
    TargetStudyArtifact.ANALOGOUS_DRIVER_TRACE,
    TargetStudyArtifact.CHANGE_PLAN,
    SourceAnalysisArtifact.SOURCE_CLOSURE,
    SourceAnalysisArtifact.STRUCTURED_C_FACTS,
)


def _git(root: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(root), *arguments], text=True, capture_output=True, check=False
    )
    if completed.returncode:
        raise WorkflowError(f"Git inspection failed: {completed.stderr.strip()}")
    return completed.stdout.strip()


class DriverImplementationService:
    def snapshot_worktree(self, project: Project, report_path: Path) -> None:
        """Freeze Codex's Git work directly; no AI response schema is involved."""

        if project.stage(MigrationStage.DRIVER_IMPLEMENTATION).status is not StageStatus.RUNNING:
            raise WorkflowError("driver_implementation must be RUNNING")
        acquisition = load_repository_acquisition(project)
        worktree = (project.root / acquisition.target_worktree.path).resolve()
        changed = set(filter(None, _git(worktree, "diff", "--name-only", "HEAD").splitlines()))
        changed.update(
            filter(None, _git(worktree, "ls-files", "--others", "--exclude-standard").splitlines())
        )
        if not changed:
            raise WorkflowError("Codex completed driver implementation without changing files")
        existing = set(
            filter(None, _git(worktree, "ls-tree", "-r", "--name-only", "HEAD").splitlines())
        )
        files = []
        for relative in sorted(changed):
            pure = PurePosixPath(relative)
            path = (worktree / pure).resolve()
            if (
                pure.is_absolute()
                or ".." in pure.parts
                or pure.as_posix() != relative
                or worktree not in path.parents
                or not path.is_file()
                or path.is_symlink()
            ):
                raise WorkflowError(f"invalid changed implementation path: {relative}")
            files.append(
                {
                    "path": relative,
                    "sha256": file_sha256(path),
                    "preexisting": relative in existing,
                }
            )
        inputs = self._inputs(project)
        report = {
            "path": str(report_path.relative_to(project.root)),
            "sha256": file_sha256(report_path),
        }
        bundle = {
            "schema_version": 1,
            "inputs": inputs,
            "target_worktree": {
                "path": acquisition.target_worktree.path,
                "base_commit": acquisition.target_worktree.base_commit,
            },
            "files": files,
            "work_report": report,
        }
        inventory = {
            "schema_version": 1,
            "inputs": inputs,
            "modified_preexisting_files": [item for item in files if item["preexisting"]],
            "new_files": [item for item in files if not item["preexisting"]],
            "work_report": report,
        }
        project.finalize_stage(
            MigrationStage.DRIVER_IMPLEMENTATION,
            (
                GeneratedArtifact(
                    MigrationArtifact.IMPLEMENTATION_BUNDLE,
                    self._json(bundle),
                    "generated:driver-implementation-snapshot",
                ),
                FileArtifact(MigrationArtifact.TRANSLATION_COVERAGE, report_path),
                GeneratedArtifact(
                    MigrationArtifact.TARGET_CHANGE_INVENTORY,
                    self._json(inventory),
                    "generated:target-change-snapshot",
                ),
            ),
        )

    @staticmethod
    def _inputs(project: Project) -> dict[str, Any]:
        dependencies = project.workflow.spec(MigrationStage.DRIVER_IMPLEMENTATION).dependencies
        result = {}
        for kind in IMPLEMENTATION_INPUTS:
            matches = [
                ref
                for stage in dependencies
                for ref in project.current_artifact_refs(stage=stage)
                if ref.kind == kind.value
            ]
            if len(matches) != 1:
                raise WorkflowError(f"driver implementation requires one {kind.value} input")
            result[kind.value] = matches[0].to_dict()
        return result

    @staticmethod
    def _json(value: dict[str, Any]) -> bytes:
        return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()


def _within(root: Path, path: Path, label: str) -> None:
    if path != root and root not in path.parents:
        raise WorkflowError(f"{label} escapes the project workspace")


def validate_implementation_bundle(context: BundleValidationContext) -> None:
    _, bundle_data = context.one_current(MigrationArtifact.IMPLEMENTATION_BUNDLE)
    bundle = json_object(bundle_data, "implementation bundle")
    inventory = json_object(
        context.one_current(MigrationArtifact.TARGET_CHANGE_INVENTORY)[1],
        "target change inventory",
    )
    report_ref, report_data = context.one_current(MigrationArtifact.TRANSLATION_COVERAGE)
    try:
        report_text = report_data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise WorkflowError("implementation work report is not non-empty UTF-8") from error
    if not report_text.strip():
        raise WorkflowError("implementation work report is not non-empty UTF-8")
    expected_inputs = {
        kind.value: context.one_dependency(kind)[0].to_dict() for kind in IMPLEMENTATION_INPUTS
    }
    if (
        bundle.get("schema_version") != 1
        or inventory.get("schema_version") != 1
        or bundle.get("inputs") != expected_inputs
        or inventory.get("inputs") != expected_inputs
    ):
        raise WorkflowError("implementation snapshot is detached from its frozen inputs")
    report = bundle.get("work_report")
    if (
        not isinstance(report, dict)
        or report.get("sha256") != report_ref.digest
        or inventory.get("work_report") != report
    ):
        raise WorkflowError("implementation snapshot is detached from its work report")
    target = bundle.get("target_worktree")
    if not isinstance(target, dict):
        raise WorkflowError("implementation snapshot has no target worktree")
    worktree = (context.project_root / str(target.get("path", ""))).resolve()
    _within(context.project_root, worktree, "target worktree")
    if _git(worktree, "rev-parse", "HEAD^{commit}") != target.get("base_commit"):
        raise WorkflowError("target worktree HEAD differs from its frozen baseline")
    changed = set(filter(None, _git(worktree, "diff", "--name-only", "HEAD").splitlines()))
    changed.update(
        filter(None, _git(worktree, "ls-files", "--others", "--exclude-standard").splitlines())
    )
    files = bundle.get("files")
    if not isinstance(files, list) or not files:
        raise WorkflowError("implementation snapshot has no changed files")
    by_path = {
        item.get("path"): item for item in files if isinstance(item, dict)
    }
    if len(by_path) != len(files) or set(by_path) != changed:
        raise WorkflowError("implementation snapshot differs from Git changed paths")
    for relative, item in by_path.items():
        pure = PurePosixPath(str(relative))
        path = (worktree / pure).resolve()
        if (
            pure.is_absolute()
            or ".." in pure.parts
            or worktree not in path.parents
            or not path.is_file()
            or path.is_symlink()
            or file_sha256(path) != item.get("sha256")
        ):
            raise WorkflowError("implementation file changed after its snapshot")
    existing = set(
        filter(None, _git(worktree, "ls-tree", "-r", "--name-only", "HEAD").splitlines())
    )
    expected_modified = [item for item in files if item.get("path") in existing]
    expected_new = [item for item in files if item.get("path") not in existing]
    if (
        inventory.get("modified_preexisting_files") != expected_modified
        or inventory.get("new_files") != expected_new
    ):
        raise WorkflowError("target change inventory differs from the Git snapshot")
