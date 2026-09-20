"""Accept the worker's natural target study; no parallel JSON-table protocol."""
from ..core.models import FileArtifact
from .contracts import TargetStudyArtifact as A, TargetStudyStage as S


class TargetStudyService:
    def accept(self, project, report):
        project.finalize_stage(S.STUDY, tuple(FileArtifact(kind, report) for kind in (
            A.PROFILE, A.STRUCTURED_PROFILE, A.API_EVIDENCE, A.ANALOGOUS_DRIVER_TRACE,
            A.CHANGE_PLAN, A.REPORT)))
