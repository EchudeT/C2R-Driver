from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..core.models import GeneratedArtifact, WorkflowError
from ..core.project import Project
from .contracts import EnvironmentArtifact


def plan_path(project: Project, route_id: str) -> Path:
    allowed = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
    if not route_id or any(character not in allowed for character in route_id):
        raise WorkflowError("route_id may contain only letters, digits, '-' and '_'")
    return project.control / "environment" / "plans" / f"{route_id}.json"


def json_bytes(value: dict[str, Any]) -> bytes:
    document = json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    return document.encode("utf-8")


def json_artifact(kind: EnvironmentArtifact, value: dict[str, Any]) -> GeneratedArtifact:
    return GeneratedArtifact(kind, json_bytes(value), f"generated:environment:{kind.value}")
