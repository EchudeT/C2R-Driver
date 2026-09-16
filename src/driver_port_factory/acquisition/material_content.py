from __future__ import annotations

import mimetypes
import re
from pathlib import Path

from .facets import RetrievalOutcome
from .retrieval_result import RetrievalFailure

_INDEXABLE_TEXT_SUFFIXES = frozenset(
    {
        ".c",
        ".cfg",
        ".h",
        ".ini",
        ".json",
        ".jsonl",
        ".md",
        ".mk",
        ".py",
        ".rs",
        ".rst",
        ".sh",
        ".toml",
        ".txt",
        ".xml",
        ".yaml",
        ".yml",
    }
)
_HTML_ERROR_PAGE = re.compile(
    rb"<(?:title|h1)[^>]*>\s*(?:error|404|403|not found|access denied)",
    re.IGNORECASE,
)


class MaterialContentPolicy:
    @staticmethod
    def media_type(path: Path) -> str:
        if path.suffix.lower() in _INDEXABLE_TEXT_SUFFIXES:
            return "text/plain"
        guessed = mimetypes.guess_type(path.name)[0]
        return guessed or "application/octet-stream"

    @staticmethod
    def validate(data: bytes, media_type: str, source: str) -> None:
        if not media_type or ";" in media_type or "/" not in media_type:
            raise RetrievalFailure(
                RetrievalOutcome.FAILED,
                f"invalid media type for {source}: {media_type!r}",
            )
        if not data:
            raise RetrievalFailure(RetrievalOutcome.FAILED, "retrieved material is empty")
        if media_type == "text/html" and _HTML_ERROR_PAGE.search(data[:16_384]):
            raise RetrievalFailure(
                RetrievalOutcome.FAILED,
                "retrieved HTML is an error or access-denied page",
            )

    @staticmethod
    def indexable(media_type: str, path: Path) -> bool:
        return media_type.startswith("text/") or path.suffix.lower() in _INDEXABLE_TEXT_SUFFIXES
