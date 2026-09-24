"""Target-framework enablement gate and immutable target-change snapshot."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..codex.contracts import CodexOutputError
from ..core.models import FileArtifact, GeneratedArtifact, StageStatus, WorkflowError
from ..core.project import Project
from ..core.validation import BundleValidationContext, json_object, require_fields
from ..knowledge.contracts import KnowledgeArtifact, KnowledgeStage
from ..target_study.contracts import TargetStudyArtifact, TargetStudyStage
from .contracts import MigrationArtifact, MigrationStage
from .implementation import worktree_files
from .review_policy import require_self_review


TARGET_FRAMEWORK_INPUTS = (
    MigrationArtifact.HANDOFF,
    MigrationArtifact.CONTRACTS,
    MigrationArtifact.TEST_PORT_MATRIX,
    TargetStudyArtifact.REPORT,
    KnowledgeArtifact.QUERY_CONTRACT,
)


def _json(value: dict[str, Any]) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()


def _input_refs(project: Project) -> dict[str, dict[str, Any]]:
    dependencies = project.workflow.spec(MigrationStage.TARGET_FRAMEWORK_ENABLEMENT).dependencies
    result: dict[str, dict[str, Any]] = {}
    for kind in TARGET_FRAMEWORK_INPUTS:
        matches = [
            ref
            for stage in dependencies
            for ref in project.current_artifact_refs(stage=stage)
            if ref.kind == kind.value
        ]
        if len(matches) != 1:
            raise WorkflowError(
                f"target framework enablement requires one {kind.value} input"
            )
        result[kind.value] = matches[0].to_dict()
    return result


def _validate_files(root: Path, target: dict[str, Any], files: list[dict[str, Any]]) -> None:
    worktree = (root / target["path"]).resolve()
    if root.resolve() not in worktree.parents:
        raise WorkflowError("target framework worktree escapes project")
    if not isinstance(files, list):
        raise WorkflowError("target framework bundle files must be a list")
    seen: set[str] = set()
    for item in files:
        if not isinstance(item, dict):
            raise WorkflowError("target framework file entry must be an object")
        path = item.get("path")
        if not isinstance(path, str) or not path or path in seen:
            raise WorkflowError("target framework file paths must be unique non-empty strings")
        seen.add(path)
        candidate = (worktree / path).resolve()
        if worktree not in candidate.parents or candidate.is_symlink():
            raise WorkflowError(f"target framework file escapes worktree or is a symlink: {path}")
        expected = item.get("sha256")
        state = item.get("state")
        if state == "file":
            if not candidate.is_file():
                raise WorkflowError(f"target framework file is missing: {path}")
            from ..knowledge.index import file_sha256
            if file_sha256(candidate) != expected:
                raise WorkflowError(f"target framework file changed after enablement: {path}")
        elif state == "deleted":
            if candidate.exists() or candidate.is_symlink():
                raise WorkflowError(f"target framework deleted file reappeared: {path}")
            if expected is not None:
                raise WorkflowError(f"deleted target framework file has a hash: {path}")
        else:
            raise WorkflowError(f"target framework file has invalid state: {path}")


def _previous_driver_files(project: Project) -> set[str]:
    """Return the last sealed driver paths when delivery is being repaired.

    A delivery-phase retry leaves the target worktree intact while invalidating
    stage occurrences.  Without this distinction the next enablement snapshot
    would absorb the already-written driver into the framework bundle.  Verify
    those files against the old snapshot first, so a framework repair cannot
    silently rewrite a driver-owned path.
    """
    refs = [
        ref for ref in project.artifact_refs(stage=MigrationStage.DRIVER_IMPLEMENTATION)
        if ref.kind == MigrationArtifact.IMPLEMENTATION_BUNDLE.value
    ]
    if not refs:
        return set()
    ref = max(refs, key=lambda item: item.ordinal or 0)
    bundle = json.loads(project.artifacts.read(ref))
    target = bundle.get("target_worktree")
    files = bundle.get("files")
    if not isinstance(target, dict) or not isinstance(files, list):
        raise WorkflowError("historical driver snapshot is malformed")
    _validate_files(project.root, target, files)
    return {item["path"] for item in files}


class TargetFrameworkEnablementService:
    """Freeze target-framework changes separately from the driver snapshot."""

    def snapshot_worktree(self, project: Project, report_path: Path) -> None:
        stage = MigrationStage.TARGET_FRAMEWORK_ENABLEMENT
        if project.stage(stage).status is not StageStatus.RUNNING:
            raise WorkflowError("target_framework_enablement must be RUNNING")
        try:
            require_self_review(report_path.read_text(encoding="utf-8"))
        except CodexOutputError as error:
            project.note_check(str(error))
        from ..acquisition.repository import load_repository_acquisition
        acquisition = load_repository_acquisition(project)
        worktree = (project.root / acquisition.target_worktree.path).resolve()
        files = worktree_files(worktree, acquisition.target_worktree.base_commit)
        driver_paths = _previous_driver_files(project)
        files = [item for item in files if item["path"] not in driver_paths]
        inputs = _input_refs(project)
        report = {
            "path": str(report_path.relative_to(project.root)),
            "sha256": _sha256(report_path),
        }
        target = {
            "path": acquisition.target_worktree.path,
            "base_commit": acquisition.target_worktree.base_commit,
        }
        bundle = {
            "schema_version": 1,
            "change_level": "target-api-framework",
            "inputs": inputs,
            "target_worktree": target,
            "files": files,
            "work_report": report,
        }
        inventory = {
            "schema_version": 1,
            "change_level": "target-api-framework",
            "inputs": inputs,
            "target_worktree": target,
            "files": files,
            "modified_preexisting_files": [item for item in files if item["preexisting"]],
            "new_files": [item for item in files if not item["preexisting"]],
            "deleted_files": [item for item in files if item["state"] == "deleted"],
            "work_report": report,
        }
        project.finalize_stage(
            stage,
            (
                GeneratedArtifact(MigrationArtifact.TARGET_FRAMEWORK_BUNDLE, _json(bundle),
                                  "generated:target-framework-enablement-snapshot"),
                FileArtifact(MigrationArtifact.TARGET_FRAMEWORK_REPORT, report_path),
                GeneratedArtifact(MigrationArtifact.TARGET_FRAMEWORK_CHANGE_INVENTORY,
                                  _json(inventory),
                                  "generated:target-framework-change-inventory"),
            ),
        )


def _sha256(path: Path) -> str:
    import hashlib
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_target_framework_bundle(context: BundleValidationContext) -> None:
    _, bundle_data = context.one_current(MigrationArtifact.TARGET_FRAMEWORK_BUNDLE)
    _, inventory_data = context.one_current(
        MigrationArtifact.TARGET_FRAMEWORK_CHANGE_INVENTORY
    )
    report_ref, report_data = context.one_current(MigrationArtifact.TARGET_FRAMEWORK_REPORT)
    bundle = json_object(bundle_data, "target framework bundle")
    inventory = json_object(inventory_data, "target framework change inventory")
    try:
        report_text = report_data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise WorkflowError("target framework report is not UTF-8") from error
    if not report_text.strip():
        raise WorkflowError("target framework report is blank")
    require_self_review(report_text)
    expected_inputs = {
        kind.value: context.one_dependency(kind)[0].to_dict()
        for kind in TARGET_FRAMEWORK_INPUTS
    }
    required = {"schema_version", "change_level", "inputs", "target_worktree", "files", "work_report"}
    for value, label in ((bundle, "target framework bundle"), (inventory, "target framework inventory")):
        require_fields(value, required, label)
        if value["schema_version"] != 1 or value["change_level"] != "target-api-framework":
            raise WorkflowError(f"{label} has an invalid schema")
        if value["inputs"] != expected_inputs or value["target_worktree"] != bundle["target_worktree"]:
            raise WorkflowError(f"{label} is detached from frozen enablement inputs")
        if value["files"] != bundle["files"] or value["work_report"] != bundle["work_report"]:
            raise WorkflowError(f"{label} is detached from target framework snapshot")
    expected_modified = [item for item in bundle["files"] if item["preexisting"]]
    expected_new = [item for item in bundle["files"] if not item["preexisting"]]
    expected_deleted = [item for item in bundle["files"] if item["state"] == "deleted"]
    if (
        inventory.get("modified_preexisting_files") != expected_modified
        or inventory.get("new_files") != expected_new
        or inventory.get("deleted_files") != expected_deleted
    ):
        raise WorkflowError("target framework change inventory differs from the snapshot")
    if bundle["work_report"].get("sha256") != report_ref.digest:
        raise WorkflowError("target framework bundle is detached from its report")
    _validate_files(context.project_root, bundle["target_worktree"], bundle["files"])
