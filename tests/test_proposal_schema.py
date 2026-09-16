from __future__ import annotations

import copy
import json
import unittest

from jsonschema import Draft202012Validator

from driver_port_factory.acquisition.facets import (
    SOURCE_DEPENDENCY_CLOSURE,
    SOURCE_DRIVER_ENTRY,
    FacetDisposition,
    GapReason,
    LocatorKind,
    MaterialRedistribution,
    required_facets,
)
from driver_port_factory.acquisition.proposal import EvidenceDiscoveryProposal
from driver_port_factory.acquisition.repository_role import RepositoryRole
from driver_port_factory.codex.prompts import default_prompt_pack_path


def proposal() -> dict[str, object]:
    facets: list[dict[str, object]] = []
    for facet in required_facets():
        if facet == SOURCE_DRIVER_ENTRY:
            facets.append(
                {
                    **facet.to_dict(),
                    "disposition": FacetDisposition.CONTROLLED.value,
                    "rationale": "frozen source entry",
                    "locators": [
                        {
                            "kind": LocatorKind.GIT_BLOB.value,
                            "repository": RepositoryRole.SOURCE.value,
                            "path": "drivers/example.c",
                            "license": "review-required",
                            "redistribution": MaterialRedistribution.UNKNOWN.value,
                            "original": True,
                        }
                    ],
                }
            )
            continue
        if facet == SOURCE_DEPENDENCY_CLOSURE:
            facets.append(
                {
                    **facet.to_dict(),
                    "disposition": FacetDisposition.CONTROLLED.value,
                    "rationale": "entry has no quoted includes",
                    "locators": [],
                }
            )
            continue
        facets.append(
            {
                **facet.to_dict(),
                "disposition": FacetDisposition.EXPLICIT_GAP.value,
                "rationale": "retrieval must establish the declared gap",
                "locators": [
                    {
                        "kind": LocatorKind.EXTERNAL_REFERENCE.value,
                        "source_url": "https://example.invalid/evidence",
                        "max_bytes": 4096,
                    }
                ],
                "gap": {
                    "reason": GapReason.NOT_FOUND.value,
                    "impact": "contract remains unsupported",
                    "repair_trigger": "authoritative evidence becomes available",
                },
            }
        )
    return {
        "schema_version": 1,
        "migration_envelope_sha256": "1" * 64,
        "repository_manifest_sha256": "2" * 64,
        "facets": facets,
    }


class EvidenceProposalSchemaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        path = default_prompt_pack_path() / "evidence-closure-proposal.schema.json"
        cls.schema = json.loads(path.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(cls.schema)
        cls.validator = Draft202012Validator(cls.schema)

    def test_schema_valid_document_is_parser_valid(self) -> None:
        document = proposal()
        self.validator.validate(document)
        parsed = EvidenceDiscoveryProposal.from_dict(document)
        self.assertEqual(parsed.migration_envelope_sha256, "1" * 64)

    def test_schema_and_parser_accept_typed_external_authorities(self) -> None:
        cases = (
            (
                4,
                {
                    "kind": LocatorKind.EXTERNAL_URL.value,
                    "source_url": "https://downloads.example.invalid/target.img",
                    "revision": "v1.0.0",
                    "expected_sha256": "3" * 64,
                    "max_bytes": 4096,
                    "authority": {
                        "kind": "repository_endorsement",
                        "repository": RepositoryRole.TARGET.value,
                        "path": "releases.txt",
                        "line_start": 1,
                        "line_end": 1,
                    },
                    "license": "review-required",
                    "redistribution": MaterialRedistribution.UNKNOWN.value,
                    "original": True,
                },
            ),
            (
                16,
                {
                    "kind": LocatorKind.EXTERNAL_URL.value,
                    "source_url": "https://vendor.example.invalid/manual.pdf",
                    "revision": "2026-01",
                    "expected_sha256": "4" * 64,
                    "max_bytes": 4096,
                    "authority": {
                        "kind": "corroborated",
                        "sources": [
                            {
                                "source_url": "https://archive.example.invalid/manual.pdf",
                                "expected_sha256": "4" * 64,
                                "max_bytes": 4096,
                            }
                        ],
                    },
                    "license": "review-required",
                    "redistribution": MaterialRedistribution.UNKNOWN.value,
                    "original": True,
                },
            ),
        )
        for index, locator in cases:
            with self.subTest(index=index):
                document = copy.deepcopy(proposal())
                facet = document["facets"][index]
                facet["disposition"] = FacetDisposition.CONTROLLED.value
                facet["locators"] = [locator]
                facet.pop("gap")
                self.validator.validate(document)
                EvidenceDiscoveryProposal.from_dict(document)

    def test_schema_rejects_removed_or_incomplete_locator_contracts(self) -> None:
        mutations = (
            lambda locator: locator.update(kind="external_input"),
            lambda locator: locator.pop("original"),
            lambda locator: locator.update(original=False, derived_from="parent"),
        )
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                document = copy.deepcopy(proposal())
                locator = document["facets"][0]["locators"][0]
                mutate(locator)
                self.assertFalse(self.validator.is_valid(document))


if __name__ == "__main__":
    unittest.main()
