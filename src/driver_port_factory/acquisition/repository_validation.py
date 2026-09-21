from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

from ..core.models import WorkflowError
from ..core.validation import BundleValidationContext
from ..intake.contracts import IntakeArtifact
from .commands import RepositoryCommandKind, RepositoryCommandRecord
from .contracts import AcquisitionArtifact
from .frozen_checkout_validation import verify_git_checkout, verify_lock
from .repository_checkout import CheckoutRecord
from .repository_manifest import RepositoryAcquisition
from .repository_role import RepositoryRole
from .revision_manifest import RepositoryPlan, RevisionManifest
from .source_identity import SourceIdentityRecord, validate_source_identity
from .writable_target_validation import WritableTargetValidator


def validate_repository_bundle(context: BundleValidationContext) -> None:
    from .revision_validation import validate_revision_bundle
    validate_revision_bundle(context)
    envelope_ref, envelope_data = context.one_dependency(IntakeArtifact.MIGRATION_ENVELOPE)
    plan_ref, plan_data = context.one_current(AcquisitionArtifact.REPOSITORY_PLAN)
    _, revision_data = context.one_current(AcquisitionArtifact.REVISION_MANIFEST)
    _, repository_data = context.one_current(AcquisitionArtifact.REPOSITORY_MANIFEST)
    _, identity_data = context.one_current(AcquisitionArtifact.SOURCE_IDENTITY_VERIFICATION)
    envelope = _json_object(envelope_data, "migration envelope")
    plan = RepositoryPlan.from_dict(json.loads(plan_data))
    revision = RevisionManifest.from_dict(json.loads(revision_data))
    acquisition = RepositoryAcquisition.from_dict(json.loads(repository_data))
    identity = SourceIdentityRecord.from_dict(json.loads(identity_data))
    if plan.migration_envelope_digest != envelope_ref.digest:
        raise WorkflowError("repository plan does not bind the real migration envelope")
    if revision.migration_envelope_sha256 != envelope_ref.digest:
        raise WorkflowError("revision manifest does not bind the real migration envelope")
    if revision.repositories != plan.repositories:
        raise WorkflowError("revision manifest repositories differ from the repository plan")
    if acquisition.repository_plan_sha256 != plan_ref.digest:
        raise WorkflowError("repository manifest does not bind its repository plan occurrence")
    if acquisition.migration_envelope_sha256 != envelope_ref.digest:
        raise WorkflowError("repository manifest does not bind the real migration envelope")
    planned = {repository.role: repository for repository in plan.repositories}
    if set(planned) != set(RepositoryRole):
        raise WorkflowError("repository plan roles are incomplete")
    checkouts = {checkout.role: checkout for checkout in acquisition.checkouts}
    if len(checkouts) != len(acquisition.checkouts) or set(checkouts) != set(RepositoryRole):
        raise WorkflowError("repository manifest checkout roles are not exact")
    for role, checkout in checkouts.items():
        spec = planned[role]
        if (
            checkout.platform,
            checkout.source_url,
            checkout.requested_ref,
            checkout.resolved_commit,
        ) != (spec.platform, spec.url, spec.requested_ref, spec.resolved_commit):
            raise WorkflowError(f"{role.value} checkout differs from repository plan")
        verify_lock(context.project_root, checkout)
        verify_git_checkout(context.project_root, checkout)
    commands = _validate_repository_commands(context.project_root, acquisition)
    WritableTargetValidator().validate(
        context.project_root,
        acquisition,
        checkouts[RepositoryRole.TARGET],
        commands[RepositoryRole.TARGET],
    )
    validate_source_identity(
        identity,
        project_root=context.project_root,
        migration_envelope_sha256=envelope_ref.digest,
        migration_envelope=envelope,
        source=checkouts[RepositoryRole.SOURCE],
    )


def _validate_repository_commands(
    root: Path,
    acquisition: RepositoryAcquisition,
) -> dict[RepositoryRole, list[RepositoryCommandRecord]]:
    by_role: dict[RepositoryRole, list[RepositoryCommandRecord]] = defaultdict(list)
    for command in acquisition.commands:
        command.verify_evidence(root)
        if Path(command.result.cwd).resolve() != root.resolve():
            raise WorkflowError("repository command used an unexpected working directory")
        by_role[command.role].append(command)
    checkouts = {checkout.role: checkout for checkout in acquisition.checkouts}
    required = {
        RepositoryCommandKind.BASELINE_FETCH,
        RepositoryCommandKind.BASELINE_COMMIT,
        RepositoryCommandKind.BASELINE_TREE,
        RepositoryCommandKind.BASELINE_WORKTREE,
        RepositoryCommandKind.BASELINE_CLEANLINESS,
    }
    for role, checkout in checkouts.items():
        operations = {command.operation for command in by_role[role]}
        if not required <= operations:
            raise WorkflowError(f"{role.value} checkout lacks required command evidence")
        _validate_baseline_command_shapes(root, checkout, by_role[role])
    return dict(by_role)


def _validate_baseline_command_shapes(
    root: Path,
    checkout: CheckoutRecord,
    commands: list[RepositoryCommandRecord],
) -> None:
    bare = str((root / checkout.bare_repository).resolve())
    worktree = str((root / checkout.checkout_path).resolve())
    expected = {
        RepositoryCommandKind.BASELINE_FETCH: (
            "git",
            "-C",
            bare,
            "fetch",
            "--depth=1",
            "--no-tags",
            "origin",
            checkout.requested_ref,
        ),
        RepositoryCommandKind.BASELINE_COMMIT: (
            "git",
            "-C",
            bare,
            "rev-parse",
            "FETCH_HEAD^{commit}",
        ),
        RepositoryCommandKind.BASELINE_TREE: (
            "git",
            "-C",
            bare,
            "rev-parse",
            f"{checkout.resolved_commit}^{{tree}}",
        ),
        RepositoryCommandKind.BASELINE_CLEANLINESS: (
            "git",
            "-C",
            worktree,
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
        ),
    }
    for operation, argv in expected.items():
        if not any(
            command.operation is operation and command.result.argv == argv for command in commands
        ):
            raise WorkflowError(
                f"{checkout.role.value} command evidence does not bind {operation.value}"
            )
    worktree_commands = [
        command.result.argv
        for command in commands
        if command.operation is RepositoryCommandKind.BASELINE_WORKTREE
    ]
    if not any(
        ("worktree", "add") == tuple(argv[3:5])
        and worktree in argv
        or argv == ("git", "-C", worktree, "rev-parse", "HEAD^{commit}")
        for argv in worktree_commands
    ):
        raise WorkflowError(f"{checkout.role.value} worktree command evidence is invalid")


def _json_object(data: bytes, label: str) -> dict[str, object]:
    try:
        value = json.loads(data)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise WorkflowError(f"{label} is not UTF-8 JSON") from error
    if not isinstance(value, dict):
        raise WorkflowError(f"{label} must be an object")
    return value
