from __future__ import annotations

import difflib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from driver_port_factory.acquisition.repository import load_repository_acquisition
from driver_port_factory.cli import main
from driver_port_factory.codex.gateway import CodexResult
from driver_port_factory.environment.contracts import EnvironmentArtifact, EnvironmentStage
from driver_port_factory.migration.contracts import MigrationArtifact, MigrationStage
from tests.test_driver_implementation import DRIVER_PATH
from tests.test_environment import qemu_fixture
from tests.test_public_qemu import (
    public_plan,
    public_qemu_project,
    qemu_reference,
    run_public,
)


def driver_failure(project) -> dict:
    document = public_plan(project)
    runtime = document["runs"][0]["artifact_sha256"]
    worktree = project.root / load_repository_acquisition(project).target_worktree.path
    checker = Path(document["runs"][0]["checker"]["argv"][1])
    checker.write_text(
        "import json, pathlib, sys\n"
        f"fixed = 'repaired' in pathlib.Path({str(worktree / DRIVER_PATH)!r}).read_text()\n"
        "print(json.dumps({"
        "'device_identity': 'example-device', 'direction': 'repaired' if fixed else 'broken', "
        "'length': 4, 'payload_sha256': 'a' * 64, 'artifact_sha256': sys.argv[1], "
        "'driver_path_observed': True, 'positive_control': True, 'negative_control': True}))\n",
        encoding="utf-8",
    )
    for run in document["runs"]:
        run["oracle"]["direction"] = "repaired"
        run["checker"]["argv"].append(runtime)
    return document


def repair_response(project, *, blocked: bool = False, outside: bool = False) -> dict:
    worktree = project.root / load_repository_acquisition(project).target_worktree.path
    path = worktree / DRIVER_PATH
    before = path.read_text(encoding="utf-8")
    after = before.replace("pub struct", "// repaired\npub struct")
    patch_text = "".join(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=f"a/{DRIVER_PATH}",
            tofile=f"b/{DRIVER_PATH}",
        )
    )
    patch_text = f"diff --git a/{DRIVER_PATH} b/{DRIVER_PATH}\n{patch_text}"
    changed = [{"path": DRIVER_PATH, "role": "DRIVER"}]
    if outside:
        patch_text = patch_text.replace(DRIVER_PATH, "Cargo.toml")
        changed = [{"path": "Cargo.toml", "role": "INTEGRATION"}]
    return {
        "schema_version": 1,
        "attribution": "QEMU_MODEL" if blocked else "DRIVER_TRANSLATION",
        "action": "BLOCKED" if blocked else "APPLY",
        "failure_run_ids": ["public-1"],
        "contract_ids": ["example-driver-behavior"],
        "test_ids": ["example-source-test"],
        "changed_paths": [] if blocked else changed,
        "patch": "" if blocked else patch_text,
        "evidence": [qemu_reference(project)],
        "rationale": "The pinned original and public controls isolate this bounded result.",
        "temporary_diagnostics_removed": not blocked,
    }


def run_repair(project, response: dict) -> int:
    result = CodexResult("repair-job", json.dumps(response), "repair-thread")
    with patch("driver_port_factory.codex.cli.CodexExecGateway.run", return_value=result):
        return main(["public-repair", "run", str(project.root)])


class PublicRepairTests(unittest.TestCase):
    def test_driver_patch_creates_new_identities_and_passes_reruns(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = public_qemu_project(Path(temporary))
            self.assertEqual(run_public(project, driver_failure(project)), 0)
            self.assertEqual(run_repair(project, repair_response(project)), 0)
            report = project.load_json_artifact(
                MigrationStage.PUBLIC_REPAIR,
                MigrationArtifact.PUBLIC_REPAIR_REPORT,
            )
            self.assertNotEqual(
                report["before_implementation_sha256"], report["after_implementation_sha256"]
            )
            self.assertEqual(report["outcome"], "PASS")

    def test_qemu_model_blocked_does_not_change_code(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = public_qemu_project(Path(temporary))
            worktree = project.root / load_repository_acquisition(project).target_worktree.path
            before = (worktree / DRIVER_PATH).read_bytes()
            route = project.load_json_artifact(
                EnvironmentStage.RECOVERY, EnvironmentArtifact.EXPERIMENT_ROUTE
            )
            qemu = Path(route["command"][0])
            marker = qemu_fixture(project.root, qmp=False)
            qemu.write_bytes(marker.read_bytes())
            qemu.chmod(0o755)
            self.assertEqual(run_public(project, public_plan(project)), 0)
            self.assertEqual(run_repair(project, repair_response(project, blocked=True)), 0)
            self.assertEqual((worktree / DRIVER_PATH).read_bytes(), before)

    def test_out_of_scope_patch_is_rejected_before_application(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = public_qemu_project(Path(temporary))
            self.assertEqual(run_public(project, driver_failure(project)), 0)
            self.assertEqual(run_repair(project, repair_response(project, outside=True)), 2)
            worktree = project.root / load_repository_acquisition(project).target_worktree.path
            self.assertNotIn("repaired", (worktree / DRIVER_PATH).read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
