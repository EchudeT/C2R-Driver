from __future__ import annotations

import unittest
from unittest.mock import Mock

from driver_port_factory.acquisition.accounting import RetrievalAttempt
from driver_port_factory.acquisition.evidence_collection import EvidenceCollector, FacetRetrieval
from driver_port_factory.acquisition.facets import (
    SOURCE_DRIVER_ENTRY,
    EvidenceFacet,
    EvidenceLane,
    FacetDisposition,
    GapReason,
    MaterialRedistribution,
    RetrievalOutcome,
)
from driver_port_factory.acquisition.locators import (
    ExternalReferenceLocator,
    GitBlobLocator,
    MaterialPolicy,
)
from driver_port_factory.acquisition.proposal import (
    FacetProposal,
    GapDeclaration,
    normalize_codex_evidence_selection,
)
from driver_port_factory.acquisition.repository_role import RepositoryRole


class SemanticEvidenceSelectionTests(unittest.TestCase):
    def test_controller_adds_control_fields_to_semantic_choices(self) -> None:
        proposal = normalize_codex_evidence_selection(
            {
                "facets": [
                    self._controlled("target", "driver_framework", "target", "kernel/pci.rs"),
                    self._controlled("qemu", "device_model", "qemu", "hw/net/ne2000.c"),
                    self._controlled("test", "source_tests", "source", "tests/ne2000.c"),
                    self._controlled("tooling", "runtime_documentation", "target", "README.md"),
                    {
                        "lane": "hardware",
                        "facet": "device_manual",
                        "rationale": "No primary manual is present in the frozen repositories",
                        "external_urls": ["https://example.invalid/ne2000-manual.pdf"],
                        "gap": {
                            "impact": "Hardware-only claims remain unsupported",
                            "repair_trigger": "A primary public manual becomes available",
                        },
                    },
                ]
            },
            migration_envelope_sha256="1" * 64,
            repository_manifest_sha256="2" * 64,
            source_driver_path="drivers/net/ne2k-pci.c",
        )

        source = next(item for item in proposal.facets if item.facet == SOURCE_DRIVER_ENTRY)
        self.assertEqual(source.disposition, FacetDisposition.CONTROLLED)
        self.assertEqual(len(source.locators), 1)
        self.assertIsInstance(source.locators[0], GitBlobLocator)
        self.assertEqual(source.locators[0].repository, RepositoryRole.SOURCE)
        self.assertEqual(source.locators[0].path, "drivers/net/ne2k-pci.c")
        hardware = next(item for item in proposal.facets if item.facet.lane.value == "hardware")
        self.assertIsInstance(hardware.locators[0], ExternalReferenceLocator)
        self.assertIsNone(hardware.gap.reason)

    def test_qemu_public_test_is_valid_test_evidence(self) -> None:
        proposal = normalize_codex_evidence_selection(
            {
                "facets": [
                    self._controlled("target", "driver_framework", "target", "kernel/pci.rs"),
                    self._controlled(
                        "qemu", "device_model", "qemu", "hw/net/ne2000.c"
                    ),
                    self._controlled(
                        "test", "ne2k_public_qtest", "qemu", "tests/qtest/ne2000-test.c"
                    ),
                    self._controlled(
                        "tooling", "runtime_documentation", "target", "README.md"
                    ),
                    {
                        "lane": "hardware",
                        "facet": "device_manual",
                        "rationale": "Primary manual is not in a frozen repository",
                        "external_urls": ["https://example.invalid/manual.pdf"],
                        "gap": {
                            "impact": "Hardware-only claims remain unsupported",
                            "repair_trigger": "A primary public manual becomes available",
                        },
                    },
                ]
            },
            migration_envelope_sha256="1" * 64,
            repository_manifest_sha256="2" * 64,
            source_driver_path="drivers/net/ne2k-pci.c",
        )

        public_test = next(
            item for item in proposal.facets if item.facet.name == "ne2k_public_qtest"
        )
        self.assertEqual(public_test.locators[0].repository, RepositoryRole.QEMU)

    def test_retrieved_material_can_document_a_semantic_gap(self) -> None:
        facet = EvidenceFacet(EvidenceLane.TEST, "ne2k_device_oracles")
        locator = GitBlobLocator(
            RepositoryRole.QEMU,
            "tests/qtest/ne2000-test.c",
            MaterialPolicy(
                "review-required",
                MaterialRedistribution.UNKNOWN,
                True,
            ),
        )
        proposal = FacetProposal(
            facet,
            FacetDisposition.EXPLICIT_GAP,
            "The existing qtest covers enumeration only",
            (locator,),
            GapDeclaration(
                None,
                "Packet and interrupt paths are untested",
                "Add a public functional test",
            ),
        )
        attempt = RetrievalAttempt(
            "test.ne2k_device_oracles.attempt.1",
            facet,
            locator,
            RetrievalOutcome.RETRIEVED,
            "retrieved frozen Git blob",
            ("test.ne2k_device_oracles.material",),
            (),
            "2026-09-17T00:00:00Z",
        )

        coverage, gaps = EvidenceCollector._account_facet(
            proposal,
            FacetRetrieval((Mock(identifier="test.ne2k_device_oracles.material"),), (attempt,)),
        )

        self.assertEqual(coverage.material_ids, ("test.ne2k_device_oracles.material",))
        self.assertEqual(gaps[0].reason, GapReason.UNAVAILABLE_PUBLIC_EVIDENCE)

    @staticmethod
    def _controlled(
        lane: str,
        facet: str,
        repository: str,
        path: str,
    ) -> dict[str, object]:
        return {
            "lane": lane,
            "facet": facet,
            "rationale": "Task-relevant frozen original",
            "repository_paths": [{"repository": repository, "path": path}],
        }


if __name__ == "__main__":
    unittest.main()
