from __future__ import annotations

import hashlib
from pathlib import Path

from ..core.ledger import canonical_json
from ..core.models import WorkflowError
from .repository_checkout import CheckoutRecord
from .repository_filesystem import git_output, workspace_directory, workspace_file


def verify_lock(root: Path, checkout: CheckoutRecord) -> None:
    path = workspace_file(root, checkout.lock_path, "repository lock")
    expected = canonical_json(
        {
            "role": checkout.role.value,
            "platform": checkout.platform,
            "source_url": checkout.source_url,
            "resolved_commit": checkout.resolved_commit,
            "tree_id": checkout.tree_id,
        }
    ).encode("utf-8")
    if (
        path.read_bytes() != expected
        or hashlib.sha256(expected).hexdigest() != checkout.lock_sha256
    ):
        raise WorkflowError(f"repository lock hash mismatch: {checkout.role.value}")


def verify_git_checkout(root: Path, checkout: CheckoutRecord) -> None:
    path = workspace_directory(root, checkout.checkout_path, "repository checkout")
    bare = workspace_directory(root, checkout.bare_repository, "bare repository")
    commit = git_output(root, "-C", str(path), "rev-parse", "HEAD^{commit}")
    tree = git_output(root, "-C", str(path), "rev-parse", "HEAD^{tree}")
    status = git_output(
        root,
        "-C",
        str(path),
        "status",
        "--porcelain=v1",
        "--untracked-files=all",
    )
    origin = git_output(root, "-C", str(bare), "remote", "get-url", "origin")
    if (
        commit != checkout.resolved_commit
        or tree != checkout.tree_id
        or origin != checkout.source_url
        or status
        or not checkout.clean
    ):
        raise WorkflowError(f"frozen {checkout.role.value} repository identity drifted")
