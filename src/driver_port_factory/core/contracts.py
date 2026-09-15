from __future__ import annotations

from typing import Protocol


class CanonicalKey(Protocol):
    """A domain-owned identifier with one stable persistence representation."""

    @property
    def value(self) -> str: ...


ArtifactKey = CanonicalKey
StageKey = CanonicalKey
EventKey = CanonicalKey
