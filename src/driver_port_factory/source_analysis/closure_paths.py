from __future__ import annotations

from pathlib import Path
from typing import Any

from ..acquisition.models import CheckoutRecord, RepositoryRole
from ..core.models import WorkflowError


def require_fields(value: Any, fields: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise WorkflowError(f"{label} must be an object")
    missing = sorted(fields - value.keys())
    if missing:
        raise WorkflowError(f"{label} missing fields: {', '.join(missing)}")
    return value


def source_path(source_root: Path, relative: Any, *, require_file: bool = True) -> Path:
    if not isinstance(relative, str) or not relative:
        raise WorkflowError("source closure path must be a non-empty string")
    path = (source_root / relative).resolve()
    if path != source_root and source_root not in path.parents:
        raise WorkflowError(f"source closure path escapes source root: {relative}")
    if require_file and not path.is_file():
        raise WorkflowError(f"source closure file does not exist: {relative}")
    if not require_file and not path.is_dir():
        raise WorkflowError(f"source include directory does not exist: {relative}")
    return path


def source_directory(source_root: Path, value: Any) -> Path:
    if not isinstance(value, str) or not value:
        raise WorkflowError("translation unit compile_directory must be a path string")
    path = Path(value)
    resolved = (path if path.is_absolute() else source_root / path).resolve()
    if resolved != source_root and source_root not in resolved.parents:
        raise WorkflowError("translation unit compile_directory escapes source baseline")
    if not resolved.is_dir():
        raise WorkflowError("translation unit compile_directory does not exist")
    return resolved


def checkout(records: tuple[CheckoutRecord, ...], role: RepositoryRole) -> CheckoutRecord:
    matches = [record for record in records if record.role is role]
    if len(matches) != 1:
        raise WorkflowError(f"expected one {role.value} checkout")
    return matches[0]
