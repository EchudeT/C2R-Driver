from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from ..core.models import WorkflowError
from ..intake.catalog import normalize_name


@dataclass(frozen=True, slots=True)
class RepositoryLocator:
    canonical_platform: str
    url: str
    default_ref: str
    selection_rule: str


class RepositoryRegistry:
    """Resolve platform names to repository locations; contains no driver knowledge."""

    def __init__(self, path: Path | None = None) -> None:
        registry_path = path or Path(__file__).parents[1] / "data" / "repositories.json"
        try:
            value = json.loads(registry_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise WorkflowError(
                f"cannot read repository registry {registry_path}: {error}"
            ) from error
        self._entries: dict[str, RepositoryLocator] = {}
        for entry in value["repositories"]:
            locator = RepositoryLocator(
                canonical_platform=entry["canonical_platform"],
                url=entry["url"],
                default_ref=entry["default_ref"],
                selection_rule=entry["selection_rule"],
            )
            for alias in (entry["canonical_platform"], *entry.get("aliases", ())):
                self._entries[normalize_name(alias)] = locator

    def lookup(self, platform: str) -> RepositoryLocator:
        try:
            return self._entries[normalize_name(platform)]
        except KeyError as error:
            raise WorkflowError(
                f"no repository registered for platform {platform!r}; "
                "provide an explicit URL and ref"
            ) from error
