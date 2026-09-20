"""Immutable intermediate compiler receipts inside the single source-analysis stage."""
import json

from ..core.models import ArtifactContent, ArtifactRef, GeneratedArtifact, StageStatus, WorkflowError
from .contracts import SourceAnalysisArtifact as A, SourceAnalysisStage as S


def latest(project):
    refs = [r for r in project.current_artifact_refs(stage=S.SOURCE_CLOSURE)
            if r.kind == A.PREPARATION.value]
    if not refs:
        return None
    return json.loads(project.artifacts.read(max(refs, key=lambda r: r.ordinal)))


def refs(project):
    receipt = latest(project)
    if receipt is None:
        raise WorkflowError("SOURCE_INPUTS_REQUIRED: submit compile_commands.json for source analysis")
    result = []
    for item in receipt["artifacts"]:
        ref = ArtifactRef(ArtifactContent(**{k: item[k] for k in
                          ("digest", "kind", "size", "cas_path")}), item["source"])
        result.append(ref)
    return result


def artifact(project, kind):
    if project.stage(S.SOURCE_CLOSURE).status is StageStatus.PASS:
        return project.artifact(S.SOURCE_CLOSURE, kind)
    matches = [r for r in refs(project) if r.kind == kind.value]
    if len(matches) != 1:
        raise WorkflowError("C_FACTS_NOT_READY: request DPF_RUN: SOURCE_ANALYSIS in the source report")
    project.artifacts.read(matches[0])  # Verify the selected immutable input.
    return matches[0]


def save(project, artifacts, *, append=False, request_sha256=None):
    artifacts = tuple(artifacts)
    replaced = {item.kind.value for item in artifacts}
    previous = [r for r in refs(project) if r.kind not in replaced] if append else []
    for item in artifacts:
        data, source = project._materialize(item)
        project.validators.validate(item.kind, data)
        previous.append(ArtifactRef(project.artifacts.put_bytes(data, kind=item.kind.value), source))
    receipt = {"artifacts": [r.to_dict() for r in previous],
               "request_sha256": latest(project).get("request_sha256") if append else request_sha256}
    if append:
        project._validate_stage_bundle(S.SOURCE_CLOSURE,
            tuple((r, project.artifacts.read(r)) for r in previous))
    project.record_artifact(S.SOURCE_CLOSURE,
        GeneratedArtifact(A.PREPARATION, json.dumps(receipt).encode(), "generated:source-preparation"))


def validate(project):
    project._validate_stage_bundle(S.SOURCE_CLOSURE,
                                  tuple((r, project.artifacts.read(r)) for r in refs(project)))


def reusable(project, database):
    import hashlib
    receipt = latest(project)
    if not receipt or receipt.get("request_sha256") != hashlib.sha256(database.read_bytes()).hexdigest():
        return False
    try:
        validate(project)
    except (WorkflowError, OSError, ValueError):
        return False
    return True


def finish(project, report):
    from ..migration.review_policy import require_self_review
    require_self_review(report.read_text())
    database = project.root / "work/stage-work/source_closure/compile_commands.json"
    if database.exists() and not reusable(project, database):
        raise WorkflowError("source inputs changed; request SOURCE_ANALYSIS before completing self-check")
    outputs = []
    import hashlib
    for ref in refs(project):
        data = project.artifacts.read(ref)
        if ref.kind == A.SOURCE_CLOSURE.value:
            data = report.read_bytes()
        elif ref.kind == A.SOURCE_CLOSURE_REPORT.value:
            value = json.loads(data)
            value["work_report"] = {"path": str(report.relative_to(project.root)),
                                    "sha256": hashlib.sha256(report.read_bytes()).hexdigest()}
            data = json.dumps(value).encode()
        outputs.append(GeneratedArtifact(A(ref.kind), data, ref.source))
    project.finalize_stage(S.SOURCE_CLOSURE, outputs)
