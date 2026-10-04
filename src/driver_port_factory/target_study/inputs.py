"""Self-contained factual analysis inputs; never summarize historical conversations."""

from ..acquisition.navigation import repositories
from ..core.models import ArtifactDirection
from ..intake.contracts import IntakeArtifact as A
from ..intake.contracts import IntakeStage as S
from .contracts import TargetStudyArtifact, TargetStudyStage


def context(project):
    request = project.load_json_artifact(S.REQUEST, A.REQUEST_RECORD)
    envelope = project.load_json_artifact(S.ENVELOPE_FREEZE, A.MIGRATION_ENVELOPE)
    identity = project.load_json_artifact(S.ENVELOPE_FREEZE, A.IDENTITY_RECORD)
    prior = [
        ref
        for ref in project.current_artifact_refs(
            stage=TargetStudyStage.STUDY, direction=ArtifactDirection.OUTPUT
        )
        if ref.kind == TargetStudyArtifact.REPORT
    ]
    return {
        "request": request["raw_request"],
        "device_identity": identity,
        "device_subset": envelope["intended_subset"],
        "excluded_device_variants": envelope["excluded_variants"],
        **repositories(project),
        "previous_analysis": [
            {
                "kind": ref.kind,
                "digest": ref.digest,
                "path": str(project.artifacts.path_for_digest(ref.digest)),
            }
            for ref in prior[-1:]
        ],
        "instruction": "This packet replaces acquisition/environment conversation history. "
        "Use current evidence references and the accepted environment route supplied with this "
        "task; raw logs remain on disk. For a repair, use its specific feedback and prior report. "
        "Do not reload past conversations or investigate every indexed document. Resolve only "
        "behavior obligations and design-changing prerequisites before implementation.",
    }
