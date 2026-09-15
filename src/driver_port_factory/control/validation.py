from __future__ import annotations

from types import MappingProxyType

from ..core.models import ActorRole, EvaluationMode
from ..core.validation import ArtifactValidator, json_object, require_fields
from .contracts import ControlArtifact


def _project_manifest(data: bytes) -> None:
    value = json_object(data, ControlArtifact.PROJECT_MANIFEST.value)
    require_fields(
        value,
        {
            "project_id",
            "source_platform",
            "target_platform",
            "driver_name",
            "evaluation_mode",
            "actor_role",
        },
        ControlArtifact.PROJECT_MANIFEST.value,
    )
    EvaluationMode(value["evaluation_mode"])
    ActorRole(value["actor_role"])


VALIDATORS = MappingProxyType[ControlArtifact, ArtifactValidator](
    {ControlArtifact.PROJECT_MANIFEST: _project_manifest}
)
