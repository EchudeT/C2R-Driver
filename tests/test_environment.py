from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from driver_port_factory.acquisition.service import AcquisitionService
from driver_port_factory.core.models import (
    ActorRole,
    EvaluationMode,
    ProjectConfig,
    StageStatus,
    WorkflowError,
)
from driver_port_factory.core.project import Project
from driver_port_factory.environment.service import EnvironmentService
from driver_port_factory.intake.service import IntakeService


def git(*arguments: str, cwd: Path) -> str:
    return subprocess.run(
        ["git", *arguments], cwd=cwd, check=True, text=True, capture_output=True
    ).stdout.strip()


def repository(root: Path, name: str, files: dict[str, str]) -> Path:
    path = root / name
    path.mkdir()
    git("init", "-b", "main", cwd=path)
    git("config", "user.name", "DPF Test", cwd=path)
    git("config", "user.email", "dpf-test@example.invalid", cwd=path)
    for relative, content in files.items():
        target = path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    git("add", ".", cwd=path)
    git("commit", "-m", "fixture", cwd=path)
    return path


def acquired_project(root: Path) -> Project:
    source = repository(root, "source", {"drivers/example.c": "/* driver */\n"})
    target = repository(
        root,
        "target",
        {
            "Cargo.toml": "[workspace]\n",
            ".github/workflows/qemu.yml": "name: qemu\n",
            "tools/run-qemu.sh": "#!/bin/sh\n",
        },
    )
    qemu = repository(root, "qemu", {"hw/test/example.c": "/* device model */\n"})
    catalog = root / "catalog.json"
    catalog.write_text(
        json.dumps(
            {
                "source_platform": "example-source",
                "catalog_id": "environment-fixture",
                "catalog_version": 1,
                "drivers": [
                    {
                        "candidate_id": "example-driver",
                        "canonical_name": "example-driver",
                        "source_entry_hint": "drivers/example.c",
                        "device_family": "Example device",
                        "bus_or_transport": "EXAMPLE-BUS",
                        "aliases": [],
                        "device_scope": ["Example device revision A"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    project = Project.initialize(
        root / "run",
        ProjectConfig(
            project_id="environment-test",
            source_platform="example-source",
            target_platform="example-target",
            driver_name="example-driver",
            evaluation_mode=EvaluationMode.DEVELOPER_EVIDENCE,
            actor_role=ActorRole.DEVELOPER,
        ),
    )
    IntakeService().analyze(
        project,
        raw_request="Port the example driver",
        catalog_paths=(catalog,),
    )
    acquisition = AcquisitionService()
    acquisition.plan(
        project,
        source_url=str(source),
        source_ref="main",
        target_url=str(target),
        target_ref="main",
        qemu_url=str(qemu),
        qemu_ref="main",
    )
    acquisition.acquire(project)
    return project


def write_plan(
    root: Path,
    *,
    route_id: str,
    command: list[str],
    route_kind: str = "qtest-or-qmp-harness",
    marker: str = "QMP_READY",
    accepted_exit_codes: list[int] | None = None,
    accept_timeout: bool = False,
    runner_evidence_paths: list[str] | None = None,
) -> Path:
    plan = {
        "schema_version": 1,
        "route_id": route_id,
        "milestone": "EXPERIMENT_READY",
        "purpose": "Execute a bounded device-model environment smoke",
        "artifact_mode": "direct-device-model",
        "route_kind": route_kind,
        "device_identity": "example-device",
        "topology": "example-bus on a machine-none smoke topology",
        "command": command,
        "cwd": ".",
        "environment": {},
        "timeout_seconds": 1,
        "expected_markers": [marker],
        "accepted_exit_codes": accepted_exit_codes or [],
        "accept_timeout": accept_timeout,
        "runner_evidence_paths": runner_evidence_paths or [".dpf/worktrees/qemu-baseline"],
        "relevance_evidence": "frozen QEMU model tree used by the planned model smoke",
        "driver_insertion_or_packaging_path": None,
    }
    path = root / f"{route_id}.json"
    path.write_text(json.dumps(plan), encoding="utf-8")
    return path


class EnvironmentRecoveryTests(unittest.TestCase):
    def test_failed_attempt_is_preserved_before_distinct_successful_route(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = acquired_project(Path(temporary))
            service = EnvironmentService()
            discovery = service.inspect(project)
            modes = {
                candidate["artifact_mode"]
                for candidate in discovery["artifact_mode_candidates"]["candidates"]
            }
            self.assertIn("source-build", modes)
            self.assertIn("ci-derived-build", modes)
            self.assertEqual(
                project.store.stage("environment_recovery").status, StageStatus.RUNNING
            )

            failed_harness = project.root / "failed-qmp-harness.py"
            failed_harness.write_text("print('different marker')\n", encoding="utf-8")
            failed_plan = write_plan(
                project.root,
                route_id="missing-marker-001",
                command=[sys.executable, str(failed_harness)],
                accepted_exit_codes=[0],
                runner_evidence_paths=[
                    ".dpf/worktrees/qemu-baseline",
                    "failed-qmp-harness.py",
                ],
            )
            service.register_plan(project, failed_plan)
            failed = service.run(project, "missing-marker-001")
            self.assertEqual(failed.readiness.value, "FAIL")
            self.assertTrue(Path(failed.attempt_path).is_file())
            self.assertEqual(
                project.store.stage("environment_recovery").status, StageStatus.RUNNING
            )

            passed_harness = project.root / "passing-qmp-harness.py"
            passed_harness.write_text("print('QMP_READY')\n", encoding="utf-8")
            passed_plan = write_plan(
                project.root,
                route_id="harness-smoke-002",
                command=[sys.executable, str(passed_harness)],
                accepted_exit_codes=[0],
                runner_evidence_paths=[
                    ".dpf/worktrees/qemu-baseline",
                    "passing-qmp-harness.py",
                ],
            )
            service.register_plan(project, passed_plan)
            passed = service.run(project, "harness-smoke-002")
            self.assertEqual(passed.readiness.value, "PASS")
            self.assertEqual(project.store.stage("environment_recovery").status, StageStatus.PASS)
            route = project.load_json_artifact("environment_recovery", "experiment_route")
            self.assertEqual(route["milestone"], "EXPERIMENT_READY")
            self.assertFalse(route["migrated_driver_runtime_ready"])

    def test_direct_qemu_route_rejects_non_qemu_executable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = acquired_project(Path(temporary))
            service = EnvironmentService()
            service.inspect(project)
            plan = write_plan(
                project.root,
                route_id="not-qemu",
                command=[sys.executable, "-c", "print('QMP_READY')"],
                route_kind="direct-qemu",
                accepted_exit_codes=[0],
            )
            with self.assertRaises(WorkflowError):
                service.register_plan(project, plan)

    @unittest.skipUnless(shutil.which("qemu-system-riscv64"), "qemu-system-riscv64 is unavailable")
    def test_real_qemu_qmp_smoke_reaches_experiment_ready(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = acquired_project(Path(temporary))
            service = EnvironmentService()
            service.inspect(project)
            plan = write_plan(
                project.root,
                route_id="real-qemu-qmp-001",
                command=[
                    "qemu-system-riscv64",
                    "-machine",
                    "none",
                    "-display",
                    "none",
                    "-nodefaults",
                    "-S",
                    "-qmp",
                    "stdio",
                ],
                route_kind="direct-qemu",
                marker='"QMP"',
                accepted_exit_codes=[0],
                accept_timeout=True,
            )
            service.register_plan(project, plan)
            result = service.run(project, "real-qemu-qmp-001")
            self.assertEqual(result.readiness.value, "PASS")
            run = project.load_json_artifact("environment_recovery", "experiment_ready_run")
            self.assertTrue(run["run"]["launched"])
            self.assertTrue(run["run"]["timed_out"] or run["run"]["exit_code"] == 0)


if __name__ == "__main__":
    unittest.main()
