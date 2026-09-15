from types import MappingProxyType

from ..core.models import WorkflowError
from ..core.validation import ArtifactValidator, json_object, json_object_document
from .contracts import EvaluationArtifact, EvaluationResult


def _result_report(data: bytes) -> None:
    value = json_object(data, "evaluation report")
    try:
        EvaluationResult(value.get("status"))
    except (TypeError, ValueError) as error:
        raise WorkflowError("evaluation report has an invalid result status") from error


_RESULT_REPORTS = {
    EvaluationArtifact.ISOLATION_REPORT,
    EvaluationArtifact.REPRODUCIBLE_BUILD_REPORT,
    EvaluationArtifact.CONTRACT_REPORT,
    EvaluationArtifact.FUNCTIONALITY_REPORT,
    EvaluationArtifact.DIFFERENTIAL_REPORT,
    EvaluationArtifact.FAULT_REPORT,
    EvaluationArtifact.MUTATION_REPORT,
    EvaluationArtifact.STRESS_REPORT,
    EvaluationArtifact.PERFORMANCE_REPORT,
    EvaluationArtifact.HARDWARE_REPORT,
    EvaluationArtifact.EVALUATION_REPORT,
    EvaluationArtifact.INDEPENDENCE_REPORT,
    EvaluationArtifact.COMMITMENT_REPORT,
    EvaluationArtifact.CLAIM_REPORT,
    EvaluationArtifact.AUDIT_REPORT,
}

VALIDATORS = MappingProxyType[EvaluationArtifact, ArtifactValidator](
    {
        artifact: _result_report if artifact in _RESULT_REPORTS else json_object_document
        for artifact in EvaluationArtifact
    }
)
