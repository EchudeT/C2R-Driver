from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from driver_port_factory.acquisition.repository_checkout import CheckoutRecord
from driver_port_factory.acquisition.repository_role import RepositoryRole
from driver_port_factory.acquisition.source_identity import (
    SourceIdentityStatus,
    SourceIdentityVerifier,
)
from driver_port_factory.core.models import WorkflowError
from driver_port_factory.intake.resolver import (
    CompositeSourceDriverResolver,
    DriverRequest,
)
from tests.acquisition_support import git, repository

NE2000_FIXTURE = Path(__file__).parents[1] / "examples" / "fixtures" / "linux-ne2000.catalog.json"


class GenericResolverTests(unittest.TestCase):
    def test_arbitrary_external_driver_catalog_needs_no_code_change(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            catalog = Path(temporary) / "serial.json"
            catalog.write_text(
                json.dumps(
                    {
                        "source_platform": "linux",
                        "catalog_id": "serial-fixture",
                        "catalog_version": 3,
                        "drivers": [
                            {
                                "candidate_id": "linux-example-uart",
                                "canonical_name": "example_uart",
                                "source_entry_hint": "drivers/tty/serial/example_uart.c",
                                "device_family": "Example UART",
                                "bus_or_transport": "MMIO",
                                "aliases": ["Example serial"],
                                "device_scope": ["Example MMIO UART"],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            resolver = CompositeSourceDriverResolver.from_catalogs("linux", (catalog,))
            result = resolver.resolve(
                DriverRequest("linux", "asterinas", "example_uart", "port UART")
            )
            self.assertTrue(result.auto_confirmable)
            self.assertEqual(result.candidates[0].bus_or_transport, "MMIO")

    def test_composite_resolver_deduplicates_provider_results(self) -> None:
        resolver = CompositeSourceDriverResolver.from_catalogs(
            "linux", (NE2000_FIXTURE, NE2000_FIXTURE)
        )
        result = resolver.resolve(
            DriverRequest(
                source_platform="linux",
                target_platform="asterinas",
                driver_name="ne2k-pci",
                raw_request="port the PCI driver",
            )
        )
        self.assertTrue(result.auto_confirmable)
        self.assertEqual(len(result.candidates), 1)
        self.assertEqual(len(result.metadata_sources), 1)

    def test_source_identity_verifier_is_platform_and_driver_agnostic(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source_root = repository(
                root,
                "source",
                {"drivers/example.c": "/* fixture */\n"},
            )
            commit = git("rev-parse", "HEAD", cwd=source_root)
            tree = git("rev-parse", "HEAD^{tree}", cwd=source_root)
            source = CheckoutRecord(
                RepositoryRole.SOURCE,
                "arbitrary-source-platform",
                source_root.as_uri(),
                commit,
                commit,
                tree,
                ".dpf/repositories/source.git",
                "source",
                True,
                ".dpf/locks/source.json",
                "1" * 64,
                "2026-09-16T00:00:00Z",
            )
            envelope = {"source_driver_entry_or_repository_hint": "drivers/example.c"}
            verified = SourceIdentityVerifier().verify(
                project_root=root,
                migration_envelope_sha256="2" * 64,
                migration_envelope=envelope,
                source=source,
            )
            self.assertIs(verified.status, SourceIdentityStatus.VERIFIED)

            escaped = {"source_driver_entry_or_repository_hint": "../outside.c"}
            with self.assertRaisesRegex(WorkflowError, "safe relative path"):
                SourceIdentityVerifier().verify(
                    project_root=root,
                    migration_envelope_sha256="2" * 64,
                    migration_envelope=escaped,
                    source=source,
                )


if __name__ == "__main__":
    unittest.main()
