from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path

from ..core.artifact_identity import substantive_artifact_identity_digest
from ..core.models import GeneratedArtifact, StageStatus, WorkflowError, utc_now
from ..core.project import Project
from ..core.validation import BundleValidationContext, json_object
from .contracts import MigrationArtifact as A
from .contracts import MigrationStage as S
from .public_qemu import latest_public_qemu_run
from .review_policy import review_policy_digest


class FinalEvidenceReviewService:
    """Store the independent AI's verdict; do not infer functional correctness."""

    @staticmethod
    def policy_digest(
        project: Project, skill_root: Path | None = None, stage=S.FINAL_EVIDENCE_REVIEW
    ) -> str:
        return review_policy_digest(project, skill_root, stage)

    @staticmethod
    def review_inputs(project: Project) -> dict:
        bundle = project.load_json_artifact(S.DRIVER_IMPLEMENTATION, A.IMPLEMENTATION_BUNDLE)
        target_framework = project.load_json_artifact(
            S.TARGET_FRAMEWORK_ENABLEMENT, A.TARGET_FRAMEWORK_BUNDLE
        )
        artifact_identity = project.load_json_artifact(S.ARTIFACT_PREPARATION, A.ARTIFACT_IDENTITY)
        public = project.load_json_artifact(S.PUBLIC_QEMU_VALIDATION, A.PUBLIC_QEMU_REPORT)
        return _review_inputs(
            bundle,
            target_framework,
            public,
            project.artifact(S.CONTRACTS, A.CONTRACTS).digest,
            project.artifact(S.CONTRACTS, A.TEST_PORT_MATRIX).digest,
            project.artifact(
                S.PUBLIC_QEMU_VALIDATION,
                A.PUBLIC_QEMU_WORK_REPORT,
            ).digest,
            target_framework_report=project.artifact(
                S.TARGET_FRAMEWORK_ENABLEMENT, A.TARGET_FRAMEWORK_REPORT
            ).digest,
            target_framework_inventory=project.artifact(
                S.TARGET_FRAMEWORK_ENABLEMENT, A.TARGET_FRAMEWORK_CHANGE_INVENTORY
            ).digest,
            implementation_report=project.artifact(
                S.DRIVER_IMPLEMENTATION, A.COMPLIANCE_REPORT
            ).digest,
            target_change_inventory=project.artifact(
                S.DRIVER_IMPLEMENTATION, A.TARGET_CHANGE_INVENTORY
            ).digest,
            artifact_identity=_artifact_identity_digest(artifact_identity),
        )

    @classmethod
    def reusable_review(cls, project: Project, *, skill_root: Path | None = None) -> dict | None:
        """Reuse only a passing independent opinion on unchanged delivery evidence."""
        inputs = cls.review_inputs(project)
        policy = cls.policy_digest(project, skill_root)
        for ref in reversed(project.artifact_refs(stage=S.FINAL_EVIDENCE_REVIEW)):
            if ref.kind != A.FINAL_EVIDENCE_REVIEW_REPORT.value:
                continue
            previous = json.loads(project.artifacts.read(ref))
            if (
                previous.get("review_mode") == "independent"
                and previous.get("outcome") == "PASS"
                and previous.get("policy_sha256") == policy
                and previous.get("review_inputs") == inputs
            ):
                return {"text": previous["review"]["text"], "source_sha256": ref.digest}
        return None

    def finalize(
        self,
        project: Project,
        *,
        review_path: Path | None = None,
        reuse: bool = False,
        policy_digest: str | None = None,
        skill_root: Path | None = None,
    ) -> None:
        current_policy = self.policy_digest(project, skill_root)
        if policy_digest is not None and policy_digest != current_policy:
            raise WorkflowError(
                "Review rules changed during execution; review current rules before acceptance"
            )
        reused = self.reusable_review(project, skill_root=skill_root) if reuse else None
        if reuse and reused is None:
            raise WorkflowError("no unchanged passing independent review to reuse")
        if review_path is None and reused is None:
            raise WorkflowError("an independent AI review is required")
        worker = project.artifact(S.PUBLIC_QEMU_VALIDATION, A.PUBLIC_QEMU_WORK_REPORT)
        text = reused["text"] if reused is not None else review_path.read_text(encoding="utf-8")
        if not text.strip():
            raise WorkflowError("independent review report is empty")
        project.artifacts.put_bytes(text.encode(), kind="independent_review")
        if project.stage(S.FINAL_EVIDENCE_REVIEW).status is StageStatus.READY:
            project.start(S.FINAL_EVIDENCE_REVIEW)
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
        project.finalize_stage(
            S.FINAL_EVIDENCE_REVIEW,
            (
                GeneratedArtifact(
                    A.FINAL_EVIDENCE_REVIEW_REPORT,
                    (
                        json.dumps(document, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
                    ).encode(),
                    "generated:independent-ai-review",
                ),
            ),
        )


def _review_inputs(
    bundle: dict,
    target_framework: dict,
    public: dict,
    contracts: str,
    tests: str,
    worker_report: str,
    *,
    target_framework_report: str,
    target_framework_inventory: str,
    implementation_report: str,
    target_change_inventory: str,
    artifact_identity: str,
) -> dict:
    run = latest_public_qemu_run(public)
    return {
        "files": bundle["files"],
        "upstream": bundle["target_worktree"],
        "target_framework": {
            "files": target_framework["files"],
            "target_worktree": target_framework["target_worktree"],
        },
        "implementation_inputs": bundle["inputs"],
        "implementation_report": implementation_report,
        "target_change_inventory": target_change_inventory,
        "contracts": contracts,
        "tests": tests,
        "worker_report": worker_report,
        "target_framework_report": target_framework_report,
        "target_framework_inventory": target_framework_inventory,
        "artifact_identity": artifact_identity,
        "runtime": run["runtime_artifact"]["sha256"],
        "script": run["script"]["sha256"],
        "helpers": run["helper_inputs"],
        # The top-level receipt and request-report fields are derived
        # metadata.  Only the captured execution evidence participates in
        # review identity, so refreshing a receipt cannot invalidate the
        # artifact and force a new QEMU run.
        "evidence_sha256": _public_evidence_digest(public),
    }


def _public_evidence_digest(public: dict) -> str:
    """Hash substantive QEMU observations, excluding derived receipt text."""
    run = deepcopy(latest_public_qemu_run(public))
    run.pop("request_report", None)
    inputs = deepcopy(public.get("inputs"))
    if isinstance(inputs, dict):
        # The identity artifact contains a human-readable work-report pointer;
        # its authoritative fields are bound separately below.
        inputs.pop(A.ARTIFACT_IDENTITY.value, None)
    return hashlib.sha256(
        json.dumps(
            {"inputs": inputs, "run": run},
            ensure_ascii=False,
            sort_keys=True,
        ).encode()
    ).hexdigest()


def _artifact_identity_digest(identity: dict) -> str:
    """Hash only authoritative packaging identity, not its report pointer."""
    return substantive_artifact_identity_digest(identity)


def validate_final_evidence_review_bundle(context: BundleValidationContext) -> None:
    report = json_object(
        context.one_current(A.FINAL_EVIDENCE_REVIEW_REPORT)[1], "final evidence review"
    )
    public = json_object(context.one_dependency(A.PUBLIC_QEMU_REPORT)[1], "public QEMU report")
    bundle = json_object(context.one_dependency(A.IMPLEMENTATION_BUNDLE)[1], "implementation")
    target_framework = json_object(
        context.one_dependency(A.TARGET_FRAMEWORK_BUNDLE)[1], "target framework enablement"
    )
    artifact_identity = json_object(
        context.one_dependency(A.ARTIFACT_IDENTITY)[1], "artifact identity"
    )
    worker_ref, _ = context.one_dependency(A.PUBLIC_QEMU_WORK_REPORT)
    expected_review_inputs = _review_inputs(
        bundle,
        target_framework,
        public,
        context.one_dependency(A.CONTRACTS)[0].digest,
        context.one_dependency(A.TEST_PORT_MATRIX)[0].digest,
        worker_ref.digest,
        target_framework_report=context.one_dependency(A.TARGET_FRAMEWORK_REPORT)[0].digest,
        target_framework_inventory=context.one_dependency(A.TARGET_FRAMEWORK_CHANGE_INVENTORY)[
            0
        ].digest,
        implementation_report=context.one_dependency(A.COMPLIANCE_REPORT)[0].digest,
        target_change_inventory=context.one_dependency(A.TARGET_CHANGE_INVENTORY)[0].digest,
        artifact_identity=_artifact_identity_digest(artifact_identity),
    )
    policy = report.get("policy_sha256", "")
    if (
        report.get("schema_version") != 4
        or report.get("outcome") != "PASS"
        or not isinstance(policy, str)
        or len(policy) != 64
        or any(c not in "0123456789abcdef" for c in policy)
        or report.get("review_inputs") != expected_review_inputs
        or report.get("worker_report_sha256") != worker_ref.digest
    ):
        raise WorkflowError("final evidence review is detached from its inputs or review policy")
    review = report.get("review", {})
    text = review.get("text", "")
    if not isinstance(text, str) or hashlib.sha256(text.encode()).hexdigest() != review.get(
        "sha256"
    ):
        raise WorkflowError("final evidence review content changed")
    if report.get("review_mode") != "independent" or not isinstance(text, str) or not text.strip():
        raise WorkflowError(
            "independent final evidence review must pass before delivery completion"
        )
