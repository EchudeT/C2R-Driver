from __future__ import annotations

import hashlib
import json
from pathlib import Path

from ..core.models import GeneratedArtifact, StageStatus, WorkflowError, utc_now
from ..core.project import Project
from ..core.validation import BundleValidationContext, json_object
from .contracts import MigrationArtifact as A, MigrationStage as S


class PublicRepairService:
    """Store the independent AI's verdict; do not infer functional correctness."""

    @staticmethod
    def policy_digest(project: Project, skill_root: Path | None = None,
                      stage=S.PUBLIC_REPAIR) -> str:
        from ..codex.prompts import SkillPromptComposer
        from ..composition import WORKFLOW_STAGE_CATALOG
        composer = SkillPromptComposer(
            skill_root or Path(project.config.skill_root), WORKFLOW_STAGE_CATALOG,
            project.workflow.stage_values,
            Path(project.config.prompt_pack) if project.config.prompt_pack else None)
        return composer.policy_digest(stage)

    @staticmethod
    def review_inputs(project: Project) -> dict:
        bundle = project.load_json_artifact(S.DRIVER_IMPLEMENTATION, A.IMPLEMENTATION_BUNDLE)
        public = project.load_json_artifact(S.PUBLIC_QEMU_VALIDATION, A.PUBLIC_QEMU_REPORT)
        return _review_inputs(bundle, public,
                              project.artifact(S.CONTRACTS, A.CONTRACTS).digest,
                              project.artifact(S.CONTRACTS, A.TEST_PORT_MATRIX).digest,
                              project.artifact(S.PUBLIC_QEMU_VALIDATION, A.PUBLIC_QEMU_WORK_REPORT).digest)

    @classmethod
    def reusable_review(cls, project: Project, *, skill_root: Path | None = None) -> dict | None:
        """Reuse only a passing independent opinion on unchanged code/plan/test inputs."""
        inputs = cls.review_inputs(project)
        policy = cls.policy_digest(project, skill_root)
        for ref in reversed(project.artifact_refs(stage=S.PUBLIC_REPAIR)):
            if ref.kind != A.PUBLIC_REPAIR_REPORT.value:
                continue
            previous = json.loads(project.artifacts.read(ref))
            if (previous.get("review_mode") == "independent"
                    and previous.get("outcome") == "PASS"
                    and previous.get("policy_sha256") == policy
                    and previous.get("review_inputs") == inputs):
                return {"text": previous["review"]["text"], "source_sha256": ref.digest}
        return None

    def finalize(self, project: Project, *, review_path: Path | None = None,
                 reuse: bool = False, policy_digest: str | None = None,
                 skill_root: Path | None = None) -> None:
        current_policy = self.policy_digest(project, skill_root)
        if policy_digest is not None and policy_digest != current_policy:
            raise WorkflowError("Review rules changed during execution; review current rules before acceptance")
        reused = self.reusable_review(project, skill_root=skill_root) if reuse else None
        if reuse and reused is None:
            raise WorkflowError("no unchanged passing independent review to reuse")
        if review_path is None and reused is None:
            raise WorkflowError("an independent AI review is required")
        worker = project.artifact(S.PUBLIC_QEMU_VALIDATION, A.PUBLIC_QEMU_WORK_REPORT)
        text = (reused["text"] if reused is not None else
                review_path.read_text(encoding="utf-8"))
        if not text.strip():
            raise WorkflowError("independent review report is empty")
        project.artifacts.put_bytes(text.encode(), kind="independent_review")
        if project.stage(S.PUBLIC_REPAIR).status is StageStatus.READY:
            project.start(S.PUBLIC_REPAIR)
        document = {
            "schema_version": 4,
            "policy_sha256": current_policy,
            "outcome": "PASS",
            "review_mode": "independent",
            "review_inputs": self.review_inputs(project),
            "reused_review_sha256": reused["source_sha256"] if reused else None,
            "review": {"text": text, "sha256": hashlib.sha256(text.encode()).hexdigest()},
            "worker_report_sha256": worker.digest,
            "recorded_at": utc_now(),
        }
        project.finalize_stage(S.PUBLIC_REPAIR, (
            GeneratedArtifact(A.PUBLIC_REPAIR_REPORT,
                (json.dumps(document, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode(),
                "generated:independent-ai-review"),
        ))


def _review_inputs(bundle: dict, public: dict, contracts: str, tests: str, worker_report: str) -> dict:
    run = public["runs"][0]
    return {"files": bundle["files"], "upstream": bundle["target_worktree"],
            "implementation_inputs": bundle["inputs"],
            "contracts": contracts, "tests": tests, "worker_report": worker_report,
            "runtime": run["runtime_artifact"]["sha256"],
            "script": run["script"]["sha256"], "helpers": run["helper_inputs"],
            "evidence_sha256": hashlib.sha256(json.dumps(public, sort_keys=True).encode()).hexdigest()}


def validate_public_repair_bundle(context: BundleValidationContext) -> None:
    report = json_object(context.one_current(A.PUBLIC_REPAIR_REPORT)[1], "evidence closure")
    public = json_object(context.one_dependency(A.PUBLIC_QEMU_REPORT)[1], "public QEMU report")
    bundle = json_object(context.one_dependency(A.IMPLEMENTATION_BUNDLE)[1], "implementation")
    worker_ref, _ = context.one_dependency(A.PUBLIC_QEMU_WORK_REPORT)
    expected_review_inputs = _review_inputs(
        bundle, public, context.one_dependency(A.CONTRACTS)[0].digest,
        context.one_dependency(A.TEST_PORT_MATRIX)[0].digest, worker_ref.digest)
    policy = report.get("policy_sha256", "")
    if (report.get("schema_version") != 4 or report.get("outcome") != "PASS"
            or not isinstance(policy, str) or len(policy) != 64
            or any(c not in "0123456789abcdef" for c in policy)
            or report.get("review_inputs") != expected_review_inputs
            or report.get("worker_report_sha256") != worker_ref.digest):
        raise WorkflowError("evidence closure is detached from its inputs or review policy")
    review = report.get("review", {})
    text = review.get("text", "")
    if (not isinstance(text, str)
            or hashlib.sha256(text.encode()).hexdigest() != review.get("sha256")):
        raise WorkflowError("evidence closure review content changed")
    if report.get("review_mode") != "independent" or not isinstance(text, str) or not text.strip():
        raise WorkflowError("independent review must pass before evidence closure")
