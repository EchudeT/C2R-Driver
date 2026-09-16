from __future__ import annotations

from dataclasses import dataclass

from ..core.models import WorkflowError
from .facets import RetrievalOutcome
from .material import EvidenceContentRef, MaterialRecord


class RetrievalFailure(WorkflowError):
    def __init__(
        self,
        outcome: RetrievalOutcome,
        detail: str,
        content_refs: tuple[EvidenceContentRef, ...] = (),
    ) -> None:
        super().__init__(detail)
        self.outcome = outcome
        self.detail = detail
        self.content_refs = content_refs

    def with_content_refs(
        self,
        *content_refs: EvidenceContentRef,
    ) -> RetrievalFailure:
        return RetrievalFailure(
            self.outcome,
            self.detail,
            (*content_refs, *self.content_refs),
        )


@dataclass(frozen=True, slots=True)
class RetrievedMaterial:
    record: MaterialRecord
    detail: str
    content_refs: tuple[EvidenceContentRef, ...] = ()
