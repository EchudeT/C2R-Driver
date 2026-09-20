from __future__ import annotations

import copy
import json
import stat
import tempfile
import unittest
from pathlib import Path

from driver_port_factory.acquisition.repository import RepositoryAcquirer
from driver_port_factory.composition import initialize_project
from driver_port_factory.core.models import (
    ActorRole,
    EvaluationMode,
    ProjectConfig,
    StageStatus,
    WorkflowError,
)
from driver_port_factory.core.project import Project
from driver_port_factory.environment.contracts import EnvironmentArtifact, EnvironmentStage
from driver_port_factory.environment.execution import ExperimentExecutor
from driver_port_factory.environment.inventory import EnvironmentInspector
from driver_port_factory.environment.models import ExperimentPlan
from driver_port_factory.environment.planning import ExperimentPlanRegistrar
from driver_port_factory.intake.service import IntakeService
from tests.acquisition_support import close_evidence, repository, select_revisions


def acquired_project(root: Path) -> Project:
    source = repository(root, "source", {"drivers/example.c": "/* driver */\n"})
    target = repository(root, "target", {"Cargo.toml": "[workspace]\n"})
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
    project = initialize_project(
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
        project, raw_request="Port the example driver", catalog_paths=(catalog,)
    )
    select_revisions(project, source, target, qemu)
    RepositoryAcquirer().acquire(project)
    close_evidence(project)
    return project


def qemu_fixture(root: Path, *, qmp: bool = True) -> Path:
    executable = root / ("qemu-system-qmp-fixture" if qmp else "qemu-system-marker-fixture")
    behavior = (
        """
import json, os, socket, sys
argument = sys.argv[sys.argv.index('-qmp') + 1]
path = argument.removeprefix('unix:').split(',server=on', 1)[0]
server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
server.bind(path)
server.listen(1)
connection, _ = server.accept()
stream = connection.makefile('rwb', buffering=0)
stream.write(json.dumps({'QMP': {'version': {'qemu': {'major': 9}}}}).encode() + b'\\r\\n')
request = json.loads(stream.readline())
stream.write(json.dumps({'return': {}, 'id': request['id']}).encode() + b'\\r\\n')
stream.readline()
connection.close()
server.close()
os.unlink(path)
"""
        if qmp
        else "print('QMP_READY')\n"
    )
    executable.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        "if '--version' in sys.argv:\n"
        "    print('QEMU emulator version 9.0.0')\n"
        "    raise SystemExit(0)\n" + behavior,
        encoding="utf-8",
    )
    executable.chmod(executable.stat().st_mode | stat.S_IXUSR)
    return executable


def write_plan(root: Path, route_id: str, executable: Path) -> Path:
    path = root / f"{route_id}.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "route_id": route_id,
                "milestone": "EXPERIMENT_READY",
                "purpose": "execute a bounded QEMU device-model smoke",
                "artifact_mode": "direct-device-model",
                "route_kind": "direct-qemu",
                "device_identity": "example-device",
                "topology": "example-bus on machine-none",
                "command": [str(executable), "-machine", "none"],
                "cwd": ".",
                "environment": {},
                "timeout_seconds": 2,
                "accepted_exit_codes": [0],
                "runner_evidence_paths": [".dpf/worktrees/qemu-baseline"],
                "relevance_evidence": "frozen QEMU model source",
                "driver_insertion_or_packaging_path": None,
            }
        ),
        encoding="utf-8",
    )
    return path


class EnvironmentRecoveryTests(unittest.TestCase):
    def test_semantic_evidence_is_preserved_without_shape_checks(self) -> None:
        value = {
            "schema_version": 3,
            "route_id": "direct-ne2k-pci-qmp-smoke",
            "milestone": "EXPERIMENT_READY",
            "purpose": {"claim": "instantiate the QEMU device model"},
            "artifact_mode": "direct-device-model",
            "device_identity": {"qemu_device": "ne2k_pci", "pci_id": "10ec:8029"},
            "topology": {"machine": "virt", "architecture": "riscv64"},
            "command": ["qemu-system-riscv64", "-machine", "virt"],
            "cwd": ".",
            "environment": {},
            "timeout_seconds": 10,
            "accepted_exit_codes": [0],
            "runner_evidence_paths": ["."],
            "relevance_evidence": {"observation": "query-pci found the device"},
        }

        plan = ExperimentPlan.from_dict(value)

        self.assertEqual(plan.device_identity, value["device_identity"])
        self.assertEqual(plan.topology, value["topology"])
        self.assertEqual(plan.relevance_evidence, value["relevance_evidence"])

    def test_marker_only_harness_fails_before_observed_execution_passes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = acquired_project(Path(temporary))
            EnvironmentInspector().inspect(project)
            script = project.root / "environment-smoke.sh"
            report = project.root / "environment-report.md"
            report.write_text("# Synthetic controller smoke, not driver evidence\n")
            script.write_text("echo QMP_READY\n")
            failed = ExperimentExecutor().run_codex_harness(
                project, script_path=script, work_report_path=report)
            self.assertEqual(failed.readiness.value, "FAIL")
            self.assertEqual(project.stage(EnvironmentStage.RECOVERY).status, StageStatus.RUNNING)
            qemu = qemu_fixture(project.root, qmp=False)
            script.write_text(f'"{qemu}" -machine none -qtest stdio\n')
            passed = ExperimentExecutor().run_codex_harness(
                project, script_path=script, work_report_path=report)
            self.assertEqual(passed.readiness.value, "PASS")
            route = project.load_json_artifact(
                EnvironmentStage.RECOVERY, EnvironmentArtifact.EXPERIMENT_ROUTE)
            self.assertFalse(route["migrated_driver_runtime_ready"])
            self.assertTrue(route["qemu_programs"])

    def test_frozen_checkout_drift_is_rejected_before_plan_or_execution(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = acquired_project(Path(temporary))
            EnvironmentInspector().inspect(project)
            qemu = qemu_fixture(project.root)
            plan = write_plan(project.root, "dirty-before-plan", qemu)
            (project.control / "worktrees/source-baseline/untracked").write_text("drift")
            with self.assertRaisesRegex(WorkflowError, "repository identity drifted"):
                ExperimentPlanRegistrar().register(project, plan)

        with tempfile.TemporaryDirectory() as temporary:
            project = acquired_project(Path(temporary))
            EnvironmentInspector().inspect(project)
            qemu = qemu_fixture(project.root)
            ExperimentPlanRegistrar().register(project, write_plan(project.root, "dirty", qemu))
            (project.control / "worktrees/qemu-baseline/hw/test/example.c").write_text("drift")
            with self.assertRaisesRegex(WorkflowError, "repository identity drifted"):
                ExperimentExecutor().run(project, "dirty")

    def test_binary_identity_tampering_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = acquired_project(Path(temporary))
            EnvironmentInspector().inspect(project)
            qemu = qemu_fixture(project.root, qmp=False)
            ExperimentPlanRegistrar().register(project, write_plan(project.root, "binary", qemu))
            qemu.write_text(qemu.read_text() + "\n# tampered\n")
            with self.assertRaisesRegex(WorkflowError, "runner changed"):
                ExperimentExecutor().run(project, "binary")


if __name__ == "__main__":
    unittest.main()
