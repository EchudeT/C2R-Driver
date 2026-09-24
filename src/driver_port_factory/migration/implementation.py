from __future__ import annotations

import json
import subprocess
from pathlib import Path, PurePosixPath
from typing import Any

from ..acquisition.repository import load_repository_acquisition
from ..codex.contracts import CodexOutputError
from ..core.models import FileArtifact, GeneratedArtifact, StageStatus, WorkflowError
from ..core.project import Project
from ..core.validation import BundleValidationContext, json_object
from ..knowledge.contracts import KnowledgeArtifact
from ..knowledge.index import file_sha256
from ..target_study.contracts import TargetStudyArtifact
from .contracts import MigrationArtifact, MigrationStage
from .review_policy import require_self_review

IMPLEMENTATION_INPUTS = (
    MigrationArtifact.HANDOFF,
    MigrationArtifact.CONTRACTS,
    MigrationArtifact.TEST_PORT_MATRIX,
    MigrationArtifact.TARGET_FRAMEWORK_BUNDLE,
    KnowledgeArtifact.QUERY_CONTRACT,
    KnowledgeArtifact.GENERATED_SKILL,
    TargetStudyArtifact.REPORT,
)


def _git(root: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(root), *arguments], text=True, capture_output=True, check=False
    )
    if completed.returncode:
        raise WorkflowError(f"Git inspection failed: {completed.stderr.strip()}")
    return completed.stdout if "-z" in arguments else completed.stdout.strip()


class ImplementationChanged(WorkflowError):
    """Source drift requires the implementation gate, not another runtime attempt."""


def worktree_files(worktree: Path, base: str) -> list[dict[str, Any]]:
    """Describe final file states relative to upstream, independently of local commits."""
    # NUL delimiters preserve spaces, newlines and non-ASCII Git paths.
    changed = set(filter(None, _git(worktree, "diff", "--no-renames", "--name-only", "-z", base).split("\0")))
    changed.update(filter(None, _git(worktree, "ls-files", "--others", "--exclude-standard", "-z").split("\0")))
    existing = set(filter(None, _git(worktree, "ls-tree", "-r", "--name-only", "-z", base).split("\0")))
    files = []
    for relative in sorted(changed):
        if relative.startswith(".dpf-output/"):
            continue
        pure = PurePosixPath(relative)
        path = worktree / pure
        if (pure.is_absolute() or ".." in pure.parts or pure.as_posix() != relative
                or worktree not in path.resolve().parents or path.is_symlink()):
            raise CodexOutputError(f"unsupported implementation path: {relative}; symlinks are not supported")
        if path.exists() and not path.is_file():
            raise CodexOutputError(f"implementation path is not a regular file: {relative}")
        if not path.exists() and relative not in existing:
            raise CodexOutputError(f"implementation path disappeared: {relative}")
        files.append({
            "path": relative,
            "preexisting": relative in existing,
            "state": "file" if path.exists() else "deleted",
            "sha256": file_sha256(path) if path.exists() else None,
            "executable": bool(path.stat().st_mode & 0o111) if path.exists() else False,
        })
    return files


def validate_worktree_snapshot(root: Path, bundle: dict) -> Path:
    """Check the complete changed-path set, not only already inventoried files."""
    worktree = (root / bundle["target_worktree"]["path"]).resolve()
    if root.resolve() not in worktree.parents:
        raise WorkflowError("implementation worktree escapes project")
    try:
        files = worktree_files(worktree, bundle["target_worktree"]["base_commit"])
        excluded = set(bundle.get("target_framework_files", ()))
        files = [item for item in files if item["path"] not in excluded]
    except CodexOutputError as error:
        raise ImplementationChanged(str(error)) from error
    if files != bundle["files"]:
        raise ImplementationChanged("implementation file states differ from snapshot")
    return worktree


class DriverImplementationService:
    def snapshot_worktree(self, project: Project, report_path: Path) -> None:
        """Freeze Codex's Git work directly; no AI response schema is involved."""

        if project.stage(MigrationStage.DRIVER_IMPLEMENTATION).status is not StageStatus.RUNNING:
            raise WorkflowError("driver_implementation must be RUNNING")
        try:
            require_self_review(report_path.read_text(encoding="utf-8"))
        except CodexOutputError as error:
            project.note_check(str(error))
        acquisition = load_repository_acquisition(project)
        worktree = (project.root / acquisition.target_worktree.path).resolve()
        framework = project.load_json_artifact(
            MigrationStage.TARGET_FRAMEWORK_ENABLEMENT,
            MigrationArtifact.TARGET_FRAMEWORK_BUNDLE,
        )
        framework_paths = {item["path"] for item in framework.get("files", ())}
        # The target worktree contains both snapshots after enablement.  Validate
        # the framework snapshot before filtering it; checking path names alone
        # would reject every legitimate framework change as an overlap, while
        # filtering without validation would let a driver edit a sealed target
        # API file pass silently.
        from .target_framework import _validate_files
        try:
            _validate_files(project.root, framework["target_worktree"], framework["files"])
        except WorkflowError as error:
            raise ImplementationChanged(str(error)) from error
        all_files = worktree_files(worktree, acquisition.target_worktree.base_commit)
        from .implementation_smoke import implementation_smoke
        smoke = implementation_smoke(project, worktree, acquisition.target_worktree.base_commit)
        files = [item for item in all_files if item["path"] not in framework_paths]
        if not files:
            project.note_check("implementation has no changes relative to frozen upstream")
        inputs = self._inputs(project)
        report = {
            "path": str(report_path.relative_to(project.root)),
            "sha256": file_sha256(report_path),
        }
        bundle = {
            "schema_version": 1,
            "inputs": inputs,
            "functional_smoke": smoke,
            "target_worktree": {
                "path": acquisition.target_worktree.path,
                "base_commit": acquisition.target_worktree.base_commit,
            },
            "files": files,
            "target_framework_files": sorted(framework_paths),
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
                FileArtifact(MigrationArtifact.COMPLIANCE_REPORT, report_path),
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
    report_ref, report_data = context.one_current(MigrationArtifact.COMPLIANCE_REPORT)
    try:
        report_text = report_data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise WorkflowError("implementation work report is not non-empty UTF-8") from error
    if not report_text.strip():
        raise WorkflowError("implementation work report is not non-empty UTF-8")
    require_self_review(report_text)
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
    files = bundle.get("files")
    if not isinstance(files, list) or not files:
        raise WorkflowError("implementation snapshot has no changed files")
    validate_worktree_snapshot(context.project_root, bundle)
    expected_modified = [item for item in files if item["preexisting"]]
    expected_new = [item for item in files if not item["preexisting"]]
    if (
        inventory.get("modified_preexisting_files") != expected_modified
        or inventory.get("new_files") != expected_new
    ):
        raise WorkflowError("target change inventory differs from the Git snapshot")
