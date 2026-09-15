from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from driver_port_factory.core.artifacts import ArtifactStore
from driver_port_factory.core.models import ArtifactContent, WorkflowError


class ArtifactStoreTests(unittest.TestCase):
    def test_put_deduplicates_and_detects_corruption(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = ArtifactStore(Path(temporary))
            first = store.put_bytes(b"same content", kind="evidence")
            second = store.put_bytes(b"same content", kind="copy")
            self.assertEqual(first.digest, second.digest)
            self.assertTrue(store.verify(first))
            (Path(temporary) / first.cas_path).write_bytes(b"corrupt")
            self.assertFalse(store.verify(first))

    def test_read_rejects_cas_path_drift_and_size_metadata_drift(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = ArtifactStore(Path(temporary) / "cas")
            content = store.put_bytes(b"controlled content", kind="evidence")
            outside = Path(temporary) / "outside"
            outside.write_bytes(b"controlled content")
            path_drift = ArtifactContent(
                content.digest,
                content.kind,
                content.size,
                "../../outside",
            )
            with self.assertRaisesRegex(WorkflowError, "non-canonical CAS path"):
                store.read(path_drift)
            size_drift = ArtifactContent(
                content.digest,
                content.kind,
                content.size + 1,
                content.cas_path,
            )
            with self.assertRaisesRegex(WorkflowError, "size metadata"):
                store.read(size_drift)


if __name__ == "__main__":
    unittest.main()
