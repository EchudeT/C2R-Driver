from __future__ import annotations

from collections.abc import Mapping
from collections.abc import Set as AbstractSet
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import urlparse

from ..core.models import WorkflowError


def exact_object(
    value: object,
    *,
    required: AbstractSet[str],
    optional: AbstractSet[str] = frozenset(),
    label: str,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise WorkflowError(f"{label} must be an object")
    keys = set(value)
    missing = sorted(required - keys)
    unexpected = sorted(keys - required - optional)
    if missing or unexpected:
        details: list[str] = []
        if missing:
            details.append("missing=" + ",".join(missing))
        if unexpected:
            details.append("unexpected=" + ",".join(unexpected))
        raise WorkflowError(f"{label} has an invalid structure: {'; '.join(details)}")
    return value


def nonempty(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise WorkflowError(f"{label} must be a non-empty string")
    return value.strip()


def string_tuple(value: object, label: str, *, allow_empty: bool = True) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise WorkflowError(f"{label} must be a string list")
    parsed = tuple(nonempty(item, label) for item in value)
    if not allow_empty and not parsed:
        raise WorkflowError(f"{label} must not be empty")
    return parsed


def sha256(value: object, label: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or value != value.lower()
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise WorkflowError(f"{label} must be lowercase SHA256")
    return value


def object_id(value: object, label: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) not in {40, 64}
        or any(character not in "0123456789abcdef" for character in value.lower())
    ):
        raise WorkflowError(f"{label} must be a full Git object ID")
    return value.lower()


def schema_version(value: Mapping[str, object], label: str) -> None:
    if value.get("schema_version") != 1:
        raise WorkflowError(f"{label} schema_version must be 1")


def relative_path(value: object, label: str) -> str:
    path = nonempty(value, label)
    candidate = PurePosixPath(path)
    if candidate.is_absolute() or ".." in candidate.parts or candidate.as_posix() in {"", "."}:
        raise WorkflowError(f"{label} must be workspace-relative")
    return candidate.as_posix()


def http_url(value: object, label: str) -> str:
    url = nonempty(value, label)
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise WorkflowError(f"{label} must use HTTP or HTTPS")
    return url


def byte_limit(value: object, label: str) -> int:
    if not isinstance(value, int) or not 0 < value <= 512 * 1024 * 1024:
        raise WorkflowError(f"{label} is outside the controlled range")
    return value
