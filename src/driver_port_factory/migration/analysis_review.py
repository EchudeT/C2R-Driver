"""An independent evidence review before the design phase is sealed."""
import hashlib
import json

from ..acquisition.contracts import AcquisitionArtifact, AcquisitionStage
from ..environment.contracts import EnvironmentArtifact, EnvironmentStage
from ..core.models import GeneratedArtifact, StageStatus, WorkflowError
from ..core.validation import json_object
from ..target_study.contracts import TargetStudyArtifact, TargetStudyStage
from .contracts import MigrationArtifact as A, MigrationStage as S
from .public_repair import PublicRepairService


INPUTS = (
    (TargetStudyStage.STUDY, TargetStudyArtifact.REPORT),
    (S.CONTRACTS, A.CONTRACTS),
    (S.CONTRACTS, A.TEST_PORT_MATRIX),
    (S.HANDOFF, A.HANDOFF),
    (AcquisitionStage.EVIDENCE_CLOSURE, AcquisitionArtifact.MATERIALS_MANIFEST),
    (AcquisitionStage.EVIDENCE_CLOSURE, AcquisitionArtifact.EVIDENCE_GAP_REGISTER),
    (EnvironmentStage.RECOVERY, EnvironmentArtifact.MODE_RECORD),
    (EnvironmentStage.RECOVERY, EnvironmentArtifact.EXPERIMENT_ROUTE),
)


class AnalysisReviewService:
    @staticmethod
    def inputs(project):
        return {kind.value: project.artifact(stage, kind).digest for stage, kind in INPUTS}

    @staticmethod
    def policy(project, skill_root=None):
        return PublicRepairService.policy_digest(project, skill_root, S.ANALYSIS_REVIEW)

    @classmethod
    def reusable(cls, project, skill_root=None):
        inputs, policy = cls.inputs(project), cls.policy(project, skill_root)
        for ref in reversed(project.artifact_refs(stage=S.ANALYSIS_REVIEW)):
            if ref.kind != A.ANALYSIS_REVIEW_REPORT.value:
                continue
            report = json.loads(project.artifacts.read(ref))
            if (report.get("inputs") == inputs and report.get("policy_sha256") == policy
                    and isinstance(report.get("text"), str) and report["text"].strip()):
                return report["text"]
        return None

    @classmethod
    def finalize(cls, project, *, text, policy_digest, skill_root=None):
        if not isinstance(text, str) or not text.strip():
            raise WorkflowError("Analysis review report is empty")
        if policy_digest != cls.policy(project, skill_root):
            raise WorkflowError("Analysis review rules changed; review current rules before acceptance")
        if project.stage(S.ANALYSIS_REVIEW).status is StageStatus.READY:
            project.start(S.ANALYSIS_REVIEW)
        value = {"schema_version": 1, "inputs": cls.inputs(project),
                 "policy_sha256": policy_digest, "text": text,
                 "text_sha256": hashlib.sha256(text.encode()).hexdigest()}
        project.finalize_stage(S.ANALYSIS_REVIEW, (GeneratedArtifact(
            A.ANALYSIS_REVIEW_REPORT, json.dumps(value, ensure_ascii=False).encode(),
            "generated:analysis-review"),))


def validate_analysis_review(context):
    value = json_object(context.one_current(A.ANALYSIS_REVIEW_REPORT)[1], "analysis review")
    inputs = {kind.value: context.one_dependency(kind)[0].digest for _, kind in INPUTS}
    text, policy = value.get("text"), value.get("policy_sha256")
    if (value.get("schema_version") != 1 or value.get("inputs") != inputs
            or not isinstance(text, str) or not text.strip()
            or hashlib.sha256(text.encode()).hexdigest() != value.get("text_sha256")
            or not isinstance(policy, str) or len(policy) != 64
            or any(c not in "0123456789abcdef" for c in policy)):
        raise WorkflowError("Analysis review must bind an explicit PASS to its inputs and rules")
