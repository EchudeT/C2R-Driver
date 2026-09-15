from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from driver_port_factory.core.artifacts import ArtifactStore


class ArtifactStoreTests(unittest.TestCase):
    def test_put_deduplicates_and_detects_corruption(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = ArtifactStore(Path(temporary))
            first = store.put_bytes(b"same content", kind="evidence", source="first")
            second = store.put_bytes(b"same content", kind="copy", source="second")
            self.assertEqual(first.digest, second.digest)
            self.assertTrue(store.verify(first))
            (Path(temporary) / first.cas_path).write_bytes(b"corrupt")
            self.assertFalse(store.verify(first))


if __name__ == "__main__":
    unittest.main()
