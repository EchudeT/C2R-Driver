"""Accept the worker's natural target study; no parallel JSON-table protocol."""
from ..core.models import FileArtifact
from .contracts import TargetStudyArtifact as A, TargetStudyStage as S


class TargetStudyService:
    def accept(self, project, report):
        project.finalize_stage(S.STUDY, (FileArtifact(A.REPORT, report),))
