"""Describe observed acceptance scope without guessing functional coverage from prose."""
import json

from ..core.models import ArtifactDirection


def evidence_summary(project) -> dict:
    result = {"execution": "NOT_RUN", "functional_assessment": "NOT_ESTABLISHED",
              "reports": {}}
    kinds = {"public_qemu_report", "public_qemu_work_report", "final_evidence_review_report"}
    for stage in project.stages():
        refs = project.current_artifact_refs(stage=stage.name, direction=ArtifactDirection.OUTPUT)
        for ref in refs:
            if ref.kind not in kinds:
                continue
            result["reports"][ref.kind] = {
                "digest": ref.digest, "path": str(project.artifacts.path_for_digest(ref.digest))}
            if ref.kind == "public_qemu_report":
                value = json.loads(project.artifacts.read(ref))
                result["execution"] = value.get("execution_status", "UNKNOWN")
                result["functional_assessment"] = "WORKER_REPORT_ONLY"
            elif ref.kind == "final_evidence_review_report" and stage.status.value == "PASS":
                result["functional_assessment"] = "INDEPENDENT_REVIEW_RECORDED"
    result["note"] = (
        "Execution PASS records the harness/collector result, not complete device behavior. "
        "Read contract/test coverage and limitations in the linked reports; "
        "review is not blind testing."
    )
    return result
