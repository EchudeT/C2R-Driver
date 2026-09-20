from __future__ import annotations

import copy
import hashlib
import json
import shutil
import tempfile
import unittest
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

from driver_port_factory.composition import open_project
from driver_port_factory.core.models import ArtifactRef, WorkflowError
from driver_port_factory.core.validation import BundleValidationContext
from driver_port_factory.source_analysis.bundle_validation import (
    validate_structured_bundle,
)
from driver_port_factory.source_analysis.contracts import (
    SourceAnalysisArtifact,
    SourceAnalysisStage,
)
from driver_port_factory.source_analysis.structured import StructuredCAnalysisService
from tests.test_source_closure import ready_project, source_closure, prepare_source, finish_source

Payloads = tuple[tuple[ArtifactRef, bytes], ...]


def json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()


class StructuredBundleFixture:
    def __init__(self, root: Path) -> None:
        project, checkouts = ready_project(root)
        closure = source_closure(project, checkouts)
        result = prepare_source(project, closure)
        if result.errors:
            raise AssertionError(result.errors)
        analysis = StructuredCAnalysisService().analyze(project, analyzer="clang")
        if analysis.errors:
            raise AssertionError(analysis.errors)
        finish_source(project)
        if list(project.control.glob("structured-c/attempts/*/units/*/ast-capture-*")):
            raise AssertionError("full AST capture was retained after projection")
        self.project_root = project.root
        self.current = self._payloads(project, SourceAnalysisStage.SOURCE_CLOSURE)
        self.dependencies = self._payloads(project, SourceAnalysisStage.SOURCE_CLOSURE)

    @staticmethod
    def _payloads(project: Any, stage: SourceAnalysisStage) -> Payloads:
        return tuple(
            (reference, project.artifacts.read(reference))
            for reference in project.artifact_refs(stage=stage)
        )

    def context(self, current: Payloads | None = None) -> BundleValidationContext:
        return BundleValidationContext(
            self.project_root,
            current if current is not None else self.current,
            self.dependencies,
        )

    def document(self, kind: SourceAnalysisArtifact) -> dict[str, Any]:
        matches = [data for reference, data in self.current if reference.kind == kind.value]
        if len(matches) != 1:
            raise AssertionError(f"fixture expected one {kind.value}")
        return json.loads(matches[0])

    def replace_document(
        self,
        payloads: Payloads,
        kind: SourceAnalysisArtifact,
        document: Any,
    ) -> Payloads:
        indexes = [
            index for index, (reference, _) in enumerate(payloads) if reference.kind == kind.value
        ]
        if len(indexes) != 1:
            raise AssertionError(f"fixture expected one {kind.value}")
        return self._replace(payloads, indexes[0], json_bytes(document))

    def replace_linked(
        self,
        payloads: Payloads,
        record: dict[str, Any],
        kind: SourceAnalysisArtifact,
        data: bytes,
    ) -> Payloads:
        source = (self.project_root / record["path"]).resolve()
        indexes = [
            index
            for index, (reference, _) in enumerate(payloads)
            if reference.kind == kind.value and Path(str(reference.source)).resolve() == source
        ]
        if len(indexes) != 1:
            raise AssertionError(f"fixture expected one linked {kind.value}")
        record["sha256"] = hashlib.sha256(data).hexdigest()
        if "size" in record:
            record["size"] = len(data)
        return self._replace(payloads, indexes[0], data)

    @staticmethod
    def _replace(payloads: Payloads, index: int, data: bytes) -> Payloads:
        items = list(payloads)
        reference, _ = items[index]
        items[index] = (
            replace(
                reference,
                content=replace(
                    reference.content,
                    digest=hashlib.sha256(data).hexdigest(),
                    size=len(data),
                ),
            ),
            data,
        )
        return tuple(items)


class StructuredBundleIntegrityTests(unittest.TestCase):
    fixture: StructuredBundleFixture
    temporary: tempfile.TemporaryDirectory[str]

    @classmethod
    def setUpClass(cls) -> None:
        if not shutil.which("clang"):
            raise unittest.SkipTest("test requires clang")
        cls.temporary = tempfile.TemporaryDirectory()
        cls.fixture = StructuredBundleFixture(Path(cls.temporary.name))

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temporary.cleanup()

    def assert_rejected(self, mutate: Callable[[StructuredBundleFixture], Payloads]) -> None:
        current = mutate(self.fixture)
        with self.assertRaises(WorkflowError):
            validate_structured_bundle(self.fixture.context(current))

    def test_persisted_baseline_and_reopen_preserve_artifact_occurrences(self) -> None:
        validate_structured_bundle(self.fixture.context())
        raw_refs = [
            reference
            for reference, _ in self.fixture.current
            if reference.kind == SourceAnalysisArtifact.STRUCTURED_C_RAW_FACT.value
        ]
        duplicate_digests = {
            reference.digest
            for reference in raw_refs
            if sum(item.digest == reference.digest for item in raw_refs) > 1
        }
        self.assertTrue(duplicate_digests)
        for digest in duplicate_digests:
            occurrences = [reference for reference in raw_refs if reference.digest == digest]
            self.assertEqual(len({reference.source for reference in occurrences}), len(occurrences))
            self.assertEqual(
                len({reference.ordinal for reference in occurrences}), len(occurrences)
            )

        reopened = open_project(self.fixture.project_root)
        current = tuple(
            (reference, reopened.artifacts.read(reference))
            for reference in reopened.artifact_refs(stage=SourceAnalysisStage.SOURCE_CLOSURE)
        )
        dependencies = tuple(
            (reference, reopened.artifacts.read(reference))
            for reference in reopened.artifact_refs(stage=SourceAnalysisStage.SOURCE_CLOSURE)
        )
        self.assertEqual(
            [reference.to_dict() for reference, _ in current],
            [reference.to_dict() for reference, _ in self.fixture.current],
        )
        validate_structured_bundle(BundleValidationContext(reopened.root, current, dependencies))

    def test_raw_bytes_cannot_be_replaced_with_synchronized_digests(self) -> None:
        def mutate(fixture: StructuredBundleFixture) -> Payloads:
            facts = fixture.document(SourceAnalysisArtifact.STRUCTURED_C_FACTS)
            raw = facts["units"][0]["raw_facts"]["preprocessed_source"]
            current = fixture.replace_linked(
                fixture.current,
                raw,
                SourceAnalysisArtifact.STRUCTURED_C_RAW_FACT,
                b"/* forged preprocessed source */\n",
            )
            return fixture.replace_document(
                current, SourceAnalysisArtifact.STRUCTURED_C_FACTS, facts
            )

        self.assert_rejected(mutate)

    def test_validation_uses_frozen_payload_not_mutable_capture(self) -> None:
        facts = self.fixture.document(SourceAnalysisArtifact.STRUCTURED_C_FACTS)
        record = facts["units"][0]["raw_facts"]["preprocessed_source"]
        path = self.fixture.project_root / record["path"]
        original = path.read_bytes()
        path.write_bytes(b"forged preprocessor output")
        try:
            # The validator consumes immutable submitted/CAS bytes, not a mutable
            # command capture. Rebinding a forged payload is tested separately.
            validate_structured_bundle(self.fixture.context())
        finally:
            path.write_bytes(original)

    def test_empty_semantic_index_is_rejected_after_digest_rebinding(self) -> None:
        def mutate(fixture: StructuredBundleFixture) -> Payloads:
            facts = fixture.document(SourceAnalysisArtifact.STRUCTURED_C_FACTS)
            record = facts["units"][0]["semantic_index"]
            current = fixture.replace_linked(
                fixture.current,
                record,
                SourceAnalysisArtifact.STRUCTURED_C_SEMANTIC_INDEX,
                json_bytes({}),
            )
            return fixture.replace_document(
                current, SourceAnalysisArtifact.STRUCTURED_C_FACTS, facts
            )

        self.assert_rejected(mutate)

    def test_missing_raw_fact_is_rejected_after_digest_rebinding(self) -> None:
        def mutate(fixture: StructuredBundleFixture) -> Payloads:
            facts = fixture.document(SourceAnalysisArtifact.STRUCTURED_C_FACTS)
            del facts["units"][0]["raw_facts"]["preprocessed_source"]
            return fixture.replace_document(
                fixture.current, SourceAnalysisArtifact.STRUCTURED_C_FACTS, facts)
        self.assert_rejected(mutate)

    def test_duplicate_or_dropped_translation_units_are_rejected(self) -> None:
        for mutation in ("duplicate", "drop"):
            with self.subTest(mutation=mutation):
                facts = self.fixture.document(SourceAnalysisArtifact.STRUCTURED_C_FACTS)
                report = self.fixture.document(SourceAnalysisArtifact.STRUCTURED_C_ANALYSIS_REPORT)
                if mutation == "duplicate":
                    facts["units"][1] = copy.deepcopy(facts["units"][0])
                else:
                    facts["units"].pop()
                    report["unit_count"] = len(facts["units"])
                current = self.fixture.replace_document(
                    self.fixture.current,
                    SourceAnalysisArtifact.STRUCTURED_C_FACTS,
                    facts,
                )
                current = self.fixture.replace_document(
                    current,
                    SourceAnalysisArtifact.STRUCTURED_C_ANALYSIS_REPORT,
                    report,
                )
                with self.assertRaises(WorkflowError):
                    validate_structured_bundle(self.fixture.context(current))

    def test_forged_aggregate_counts_and_report_unit_count_are_rejected(self) -> None:
        facts = self.fixture.document(SourceAnalysisArtifact.STRUCTURED_C_FACTS)
        facts["semantic_counts"]["functions"] += 1
        current = self.fixture.replace_document(
            self.fixture.current,
            SourceAnalysisArtifact.STRUCTURED_C_FACTS,
            facts,
        )
        with self.assertRaises(WorkflowError):
            validate_structured_bundle(self.fixture.context(current))

        report = self.fixture.document(SourceAnalysisArtifact.STRUCTURED_C_ANALYSIS_REPORT)
        report["unit_count"] += 1
        current = self.fixture.replace_document(
            self.fixture.current,
            SourceAnalysisArtifact.STRUCTURED_C_ANALYSIS_REPORT,
            report,
        )
        with self.assertRaises(WorkflowError):
            validate_structured_bundle(self.fixture.context(current))

    def test_cross_unit_semantic_artifacts_cannot_be_swapped(self) -> None:
        facts = self.fixture.document(SourceAnalysisArtifact.STRUCTURED_C_FACTS)
        first = facts["units"][0]["semantic_index"]
        second = facts["units"][1]["semantic_index"]
        first["path"], second["path"] = second["path"], first["path"]
        first["sha256"], second["sha256"] = second["sha256"], first["sha256"]
        current = self.fixture.replace_document(
            self.fixture.current,
            SourceAnalysisArtifact.STRUCTURED_C_FACTS,
            facts,
        )
        with self.assertRaises(WorkflowError):
            validate_structured_bundle(self.fixture.context(current))

    def test_source_and_analyzer_identity_forgery_is_rejected(self) -> None:
        for field in ("source_checkout_identity", "analyzer"):
            with self.subTest(field=field):
                facts = self.fixture.document(SourceAnalysisArtifact.STRUCTURED_C_FACTS)
                if field == "source_checkout_identity":
                    facts[field]["tree_id"] = "0" * 40
                else:
                    facts[field]["sha256"] = "0" * 64
                current = self.fixture.replace_document(
                    self.fixture.current,
                    SourceAnalysisArtifact.STRUCTURED_C_FACTS,
                    facts,
                )
                with self.assertRaises(WorkflowError):
                    validate_structured_bundle(self.fixture.context(current))


if __name__ == "__main__":
    unittest.main()
