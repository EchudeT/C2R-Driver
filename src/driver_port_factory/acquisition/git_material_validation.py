from __future__ import annotations

from pathlib import Path

from ..core.models import WorkflowError
from .material import GitBlobOrigin, MaterialRecord
from .repository_filesystem import git_bytes, git_output
from .repository_manifest import RepositoryAcquisition


def validate_git_material_identity(
    root: Path,
    acquisition: RepositoryAcquisition,
    record: MaterialRecord,
    material_path: Path,
    origin: GitBlobOrigin,
) -> None:
    checkout = acquisition.checkout(origin.repository)
    checkout_root = root / checkout.checkout_path
    expected = (checkout_root / origin.path).resolve()
    if (
        material_path != expected
        or origin.commit != checkout.resolved_commit
        or record.source_url != checkout.source_url
        or record.revision != checkout.resolved_commit
    ):
        raise WorkflowError("Git material provenance differs from frozen checkout")
    blob = git_output(
        root,
        "-C",
        str(checkout_root),
        "rev-parse",
        f"{checkout.resolved_commit}:{origin.path}",
    )
    if blob != origin.blob:
        raise WorkflowError("Git material blob identity drifted")
    content = git_bytes(root, "-C", str(checkout_root), "cat-file", "blob", blob)
    if content != material_path.read_bytes():
        raise WorkflowError("Git material bytes differ from the frozen blob")
