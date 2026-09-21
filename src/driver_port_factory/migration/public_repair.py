from __future__ import annotations

import hashlib
import json
from pathlib import Path

from ..core.models import GeneratedArtifact, StageStatus, WorkflowError, utc_now
from ..core.project import Project
from ..core.validation import BundleValidationContext, json_object
from .contracts import MigrationArtifact as A, MigrationStage as S
from .review_policy import require_self_review, review_decision


class PublicRepairService:
    """Close evidence honestly: worker self-check or risk-triggered independent review."""

    @staticmethod
    def decision(project: Project) -> dict:
        bundle = project.load_json_artifact(S.DRIVER_IMPLEMENTATION, A.IMPLEMENTATION_BUNDLE)
        reports = tuple(project.artifacts.read(project.artifact(stage, kind)).decode()
                        for stage, kind in ((S.DRIVER_IMPLEMENTATION, A.COMPLIANCE_REPORT),
                                            (S.PUBLIC_QEMU_VALIDATION, A.PUBLIC_QEMU_WORK_REPORT)))
        from .implementation import ImplementationChanged
        try:
            return review_decision(project.root, bundle, reports)
        except ImplementationChanged as error:
            return {"policy": "rust-change-impact-or-explicit-request",
                    "independent_required": True,
                    "reasons": [{"kind": "implementation_drift", "detail": str(error)}]}

    @staticmethod
    def review_inputs(project: Project) -> dict:
        bundle = project.load_json_artifact(S.DRIVER_IMPLEMENTATION, A.IMPLEMENTATION_BUNDLE)
        public = project.load_json_artifact(S.PUBLIC_QEMU_VALIDATION, A.PUBLIC_QEMU_REPORT)
        return _review_inputs(bundle, public,
                              project.artifact(S.CONTRACTS, A.CONTRACTS).digest,
                              project.artifact(S.CONTRACTS, A.TEST_PORT_MATRIX).digest)

    @classmethod
    def reusable_review(cls, project: Project) -> dict | None:
        """Reuse only a passing independent opinion on unchanged code/plan/test inputs."""
        inputs, decision = cls.review_inputs(project), cls.decision(project)
        for ref in reversed(project.artifact_refs(stage=S.PUBLIC_REPAIR)):
            if ref.kind != A.PUBLIC_REPAIR_REPORT.value:
                continue
            previous = json.loads(project.artifacts.read(ref))
            if (previous.get("review_mode") == "independent"
                    and previous.get("outcome") == "PASS"
                    and previous.get("review_inputs") == inputs
                    and previous.get("decision") == decision):
                return {"text": previous["review"]["text"], "source_sha256": ref.digest}
        return None

    def finalize(self, project: Project, *, review_path: Path | None = None,
                 reuse: bool = False) -> None:
        decision = self.decision(project)
        reused = self.reusable_review(project) if reuse else None
        if reuse and reused is None:
            raise WorkflowError("no unchanged passing independent review to reuse")
        if decision["independent_required"] and review_path is None and reused is None:
            project.note_check("Risk checker recommends independent review: " + json.dumps(decision))
        worker = project.artifact(S.PUBLIC_QEMU_VALIDATION, A.PUBLIC_QEMU_WORK_REPORT)
        text = (reused["text"] if reused is not None else
                review_path.read_text(encoding="utf-8") if review_path is not None
                else project.artifacts.read(worker).decode())
        if project.stage(S.PUBLIC_REPAIR).status is StageStatus.READY:
            project.start(S.PUBLIC_REPAIR)
        document = {
            "schema_version": 3,
            "outcome": "PASS",
            "review_mode": "independent" if review_path is not None or reused else "worker_self_check",
            "review_inputs": self.review_inputs(project),
            "reused_review_sha256": reused["source_sha256"] if reused else None,
            "decision": decision,
            "review": {"text": text, "sha256": hashlib.sha256(text.encode()).hexdigest()},
            "worker_report_sha256": worker.digest,
            "recorded_at": utc_now(),
        }
        project.finalize_stage(S.PUBLIC_REPAIR, (
            GeneratedArtifact(A.PUBLIC_REPAIR_REPORT,
                (json.dumps(document, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode(),
                "generated:risk-triggered-evidence-closure"),
        ))


def _review_inputs(bundle: dict, public: dict, contracts: str, tests: str) -> dict:
    run = public["runs"][0]
    return {"files": bundle["files"], "upstream": bundle["target_worktree"],
            "contracts": contracts, "tests": tests,
            "runtime": run["runtime_artifact"]["sha256"],
            "script": run["script"]["sha256"], "helpers": run["helper_inputs"]}


def validate_public_repair_bundle(context: BundleValidationContext) -> None:
    report = json_object(context.one_current(A.PUBLIC_REPAIR_REPORT)[1], "evidence closure")
    public = json_object(context.one_dependency(A.PUBLIC_QEMU_REPORT)[1], "public QEMU report")
    if (public.get("execution_status") != "PASS"
            and S.PUBLIC_QEMU_VALIDATION.value not in context.worker_accepted_dependencies):
        raise WorkflowError("evidence closure cannot accept a failed public run")
    bundle = json_object(context.one_dependency(A.IMPLEMENTATION_BUNDLE)[1], "implementation")
    self_check = context.one_dependency(A.COMPLIANCE_REPORT)[1].decode()
    worker_ref, worker_bytes = context.one_dependency(A.PUBLIC_QEMU_WORK_REPORT)
    worker_text = worker_bytes.decode()
    require_self_review(self_check)
    require_self_review(worker_text)
    decision = review_decision(context.project_root, bundle, (self_check, worker_text))
    expected_review_inputs = _review_inputs(
        bundle, public, context.one_dependency(A.CONTRACTS)[0].digest,
        context.one_dependency(A.TEST_PORT_MATRIX)[0].digest)
    if (report.get("schema_version") != 3 or report.get("outcome") != "PASS"
            or report.get("decision") != decision
            or report.get("review_inputs") != expected_review_inputs
            or report.get("worker_report_sha256") != worker_ref.digest):
        raise WorkflowError("evidence closure is detached from its inputs or review policy")
    review = report.get("review", {})
    text = review.get("text", "")
    if (not isinstance(text, str)
            or hashlib.sha256(text.encode()).hexdigest() != review.get("sha256")):
        raise WorkflowError("evidence closure review content changed")
    mode = report.get("review_mode")
    if mode == "worker_self_check":
        if decision["independent_required"] or text != worker_text:
            raise WorkflowError("worker self-check cannot replace required independent review")
    elif mode == "independent":
        if not text.rstrip().endswith("\nDPF_REVIEW: PASS"):
            raise WorkflowError("independent review must pass before evidence closure")
    else:
        raise WorkflowError("evidence closure has an invalid review mode")
