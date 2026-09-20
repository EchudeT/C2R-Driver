from __future__ import annotations

import hashlib
from typing import Any

from ..core.models import WorkflowError
from ..core.validation import BundleValidationContext, json_object, json_value
from .bundle_artifacts import ArtifactPayload, StructuredArtifactInventory
from .bundle_inputs import FrozenAnalysisInputs
from .bundle_units import TranslationUnitBundleValidator
from .contracts import SourceAnalysisArtifact
from .fact_model import StructuredAnalysisStatus


class StructuredBundleValidator:
    """Compose the independent proofs required by the structured-C phase gate."""

    def __init__(self, context: BundleValidationContext) -> None:
        self.inventory = StructuredArtifactInventory(context)

    def validate(self) -> None:
        facts_payload = self.inventory.current_one(SourceAnalysisArtifact.STRUCTURED_C_FACTS)
        report_payload = self.inventory.current_one(
            SourceAnalysisArtifact.STRUCTURED_C_ANALYSIS_REPORT
        )
        compile_payload = self.inventory.current_one(SourceAnalysisArtifact.COMPILE_MANIFEST)
        database_payload = self.inventory.current_one(
            SourceAnalysisArtifact.COMPILATION_DATABASE
        )
        facts = json_object(facts_payload.data, facts_payload.ref.kind)
        report = json_object(report_payload.data, report_payload.ref.kind)
        compile_manifest = json_object(compile_payload.data, compile_payload.ref.kind)
        compilation_database = json_value(database_payload.data, database_payload.ref.kind)
        if not isinstance(compilation_database, list):
            raise WorkflowError("compilation_database must be an array")

        source_root = FrozenAnalysisInputs(self.inventory.project_root).validate(
            facts,
            compile_manifest,
            compilation_database,
            compile_payload,
            database_payload,
        )
        self._validate_report(
            report,
            facts,
            facts_payload,
            report_payload,
            compile_payload.data,
            database_payload.data,
        )
        aggregate = TranslationUnitBundleValidator(self.inventory).validate(
            facts, compile_manifest, source_root
        )
        if facts.get("semantic_counts") != dict(sorted(aggregate.items())):
            raise WorkflowError("top-level semantic counts do not equal the unit totals")
        self.inventory.validate_consumption()

    def _validate_report(
        self,
        report: dict[str, Any],
        facts: dict[str, Any],
        facts_payload: ArtifactPayload,
        report_payload: ArtifactPayload,
        compile_bytes: bytes,
        database_bytes: bytes,
    ) -> None:
        try:
            status = StructuredAnalysisStatus(report.get("status"))
        except (TypeError, ValueError) as error:
            raise WorkflowError("structured analysis report has invalid status") from error
        if status is not StructuredAnalysisStatus.READY:
            raise WorkflowError("structured analysis report is not READY")
        if report.get("errors") != []:
            raise WorkflowError("structured analysis report contains errors")
        units = facts.get("units")
        if not isinstance(units, list) or report.get("unit_count") != len(units):
            raise WorkflowError("structured analysis report unit count differs from facts")
        expected_input = hashlib.sha256(compile_bytes + database_bytes).hexdigest()
        if report.get("input_sha256") != expected_input:
            raise WorkflowError("structured analysis report input identity differs from closure")
        attempt_path = self.inventory.project_path(report.get("attempt_path"), "attempt path")
        if facts_payload.source_path.parent != attempt_path:
            raise WorkflowError("structured facts are outside their declared attempt")
        if report_payload.source_path.parent != attempt_path:
            raise WorkflowError("structured report is outside its declared attempt")


def validate_structured_bundle(context: BundleValidationContext) -> None:
    StructuredBundleValidator(context).validate()
