from types import MappingProxyType

from ..core.validation import ArtifactValidator, nonempty, utf8_document
from .contracts import CodexArtifact

VALIDATORS = MappingProxyType[CodexArtifact, ArtifactValidator](
    {
        CodexArtifact.PROMPT: utf8_document,
        CodexArtifact.JOB_RESULT: utf8_document,
        CodexArtifact.WORK_REPORT: utf8_document,
        CodexArtifact.EVENT_LOG: nonempty,
    }
)
