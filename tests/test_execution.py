from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from driver_port_factory.core.execution import CommandRunner


class CommandRunnerTests(unittest.TestCase):
    def test_records_stdout_and_exit_code(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            result = CommandRunner(root / "runs").run(
                ["python3", "-c", "print('driver-ok')"], cwd=root
            )
            self.assertEqual(result.exit_code, 0)
            self.assertFalse(result.timed_out)
            self.assertEqual(Path(result.stdout_path).read_text().strip(), "driver-ok")


if __name__ == "__main__":
    unittest.main()
