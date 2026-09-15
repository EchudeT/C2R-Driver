from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from driver_port_factory.acquisition.contracts import AcquisitionArtifact, AcquisitionStage
from driver_port_factory.acquisition.execution import EvidenceAcquirer
from driver_port_factory.acquisition.planning import AcquisitionPlanner
from driver_port_factory.acquisition.verification import AcquisitionVerifier
from driver_port_factory.composition import initialize_project
from driver_port_factory.core.models import (
    ActorRole,
    EvaluationMode,
    ProjectConfig,
    StageStatus,
    WorkflowError,
)
from driver_port_factory.environment.contracts import EnvironmentStage
from driver_port_factory.intake.service import IntakeService


def git(*arguments: str, cwd: Path) -> str:
    return subprocess.run(
        ["git", *arguments],
        cwd=cwd,
        check=True,
        text=True,
        capture_output=True,
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


def project_config() -> ProjectConfig:
    return ProjectConfig(
        project_id="acquisition-test",
        source_platform="linux",
        target_platform="asterinas",
        driver_name="example-driver",
        evaluation_mode=EvaluationMode.DEVELOPER_EVIDENCE,
        actor_role=ActorRole.DEVELOPER,
    )


class AcquisitionTests(unittest.TestCase):
    def test_plan_is_rejected_before_driver_scope_is_frozen(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = initialize_project(Path(temporary) / "run", project_config())
            with self.assertRaises(WorkflowError):
                AcquisitionPlanner().plan(project)

    def test_local_repositories_are_pinned_acquired_and_verified(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = repository(
                root,
                "source-repo",
                {"drivers/example.c": "/* source driver */\n"},
            )
            target = repository(root, "target-repo", {"README.md": "target\n"})
            qemu = repository(root, "qemu-repo", {"hw/example.c": "/* model */\n"})
            catalog = root / "drivers.json"
            catalog.write_text(
                json.dumps(
                    {
                        "source_platform": "linux",
                        "catalog_id": "generic-acquisition-fixture",
                        "catalog_version": 1,
                        "drivers": [
                            {
                                "candidate_id": "linux-example",
                                "canonical_name": "example-driver",
                                "source_entry_hint": "drivers/example.c",
                                "device_family": "Example device",
                                "bus_or_transport": "TESTBUS",
                                "aliases": [],
                                "device_scope": ["Example test device"],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            project = initialize_project(root / "run", project_config())
            IntakeService().analyze(
                project,
                raw_request="port an arbitrary example driver",
                catalog_paths=(catalog,),
            )
            plan = AcquisitionPlanner().plan(
                project,
                source_url=str(source),
                source_ref="main",
                target_url=str(target),
                target_ref="main",
                qemu_url=str(qemu),
                qemu_ref="main",
            )
            self.assertEqual(len(plan.repositories), 3)
            self.assertTrue(
                all(len(repository.resolved_commit) == 40 for repository in plan.repositories)
            )
            self.assertEqual(
                project.stage(AcquisitionStage.REVISION_SELECTION).status,
                StageStatus.PASS,
            )
            result = EvidenceAcquirer().acquire(project)
            self.assertEqual(result.status, StageStatus.PASS)
            self.assertTrue(result.source_identity_consistent)
            self.assertTrue((project.root / result.target_worktree / ".git").exists())
            materials_ref = project.artifact(
                AcquisitionStage.EVIDENCE_ACQUISITION,
                AcquisitionArtifact.MATERIALS_MANIFEST,
            )
            materials = [
                json.loads(line)
                for line in project.artifacts.read(materials_ref).decode().splitlines()
            ]
            self.assertTrue(
                all((project.root / material["path"]).is_file() for material in materials)
            )
            self.assertEqual(
                project.stage(EnvironmentStage.RECOVERY).status,
                StageStatus.READY,
            )
            verification = AcquisitionVerifier().verify(project)
            self.assertTrue(verification["valid"])
            self.assertEqual(len(verification["repositories"]), 3)
            self.assertTrue(project.verify_event_chain())

    def test_ref_change_after_plan_is_recorded_as_failed_acquisition(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = repository(root, "source", {"drivers/example.c": "v1\n"})
            target = repository(root, "target", {"README": "target\n"})
            qemu = repository(root, "qemu", {"README": "qemu\n"})
            catalog = root / "catalog.json"
            catalog.write_text(
                json.dumps(
                    {
                        "source_platform": "linux",
                        "drivers": [
                            {
                                "candidate_id": "example",
                                "canonical_name": "example-driver",
                                "source_entry_hint": "drivers/example.c",
                                "device_family": "Example",
                                "bus_or_transport": "TESTBUS",
                                "aliases": [],
                                "device_scope": ["Example"],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            project = initialize_project(root / "run", project_config())
            IntakeService().analyze(
                project,
                raw_request="port example",
                catalog_paths=(catalog,),
            )
            AcquisitionPlanner().plan(
                project,
                source_url=str(source),
                source_ref="main",
                target_url=str(target),
                target_ref="main",
                qemu_url=str(qemu),
                qemu_ref="main",
            )
            (source / "drivers/example.c").write_text("v2\n", encoding="utf-8")
            git("add", ".", cwd=source)
            git("commit", "-m", "move ref after plan", cwd=source)
            with self.assertRaises(WorkflowError):
                EvidenceAcquirer().acquire(project)
            self.assertEqual(
                project.stage(AcquisitionStage.EVIDENCE_ACQUISITION).status,
                StageStatus.FAIL,
            )
            failure = project.load_json_artifact(
                AcquisitionStage.EVIDENCE_ACQUISITION,
                AcquisitionArtifact.ACQUISITION_ATTEMPT,
            )
            self.assertTrue(failure["partial_paths_preserved"])


if __name__ == "__main__":
    unittest.main()
