from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

from driver_port_factory.acquisition.repository import load_repository_acquisition
from driver_port_factory.cli import main
from driver_port_factory.core.models import StageStatus
from driver_port_factory.environment.contracts import EnvironmentArtifact, EnvironmentStage
from driver_port_factory.migration.contracts import MigrationArtifact, MigrationStage
from tests.test_target_compliance import (
    compliance_project,
    compliance_response,
    run_compliance,
)

BUILDER = """\
import json
import pathlib
import sys

base, final, packaged, worktree, payload_file = map(pathlib.Path, sys.argv[1:])
payloads = json.loads(payload_file.read_text())["payloads"]
content = base.read_bytes()
tests = b""
for payload in payloads:
    data = (worktree / payload["path"]).read_bytes()
    content += b"\\n" + data
    if payload["role"] == "PUBLIC_TEST":
        tests += data
final.parent.mkdir(parents=True, exist_ok=True)
final.write_bytes(content)
packaged.write_bytes(tests)
"""

STALE_BUILDER = """\
import pathlib
import sys

base, final, packaged, _worktree, _payload_file = map(pathlib.Path, sys.argv[1:])
final.parent.mkdir(parents=True, exist_ok=True)
final.write_bytes(base.read_bytes())
packaged.write_bytes(b"stale test")
"""

INSPECTOR = """\
import hashlib
import json
import pathlib
import sys

final, packaged, worktree, payload_file, manifest = map(pathlib.Path, sys.argv[1:])
template = json.loads(payload_file.read_text())
final_data = final.read_bytes()
test_data = packaged.read_bytes()
for payload in template["payloads"]:
    data = (worktree / payload["path"]).read_bytes()
    if data not in final_data or (payload["role"] == "PUBLIC_TEST" and data not in test_data):
        raise SystemExit(3)
template.update(
    schema_version=1,
    final_artifact_sha256=hashlib.sha256(final_data).hexdigest(),
    packaged_test_sha256=hashlib.sha256(test_data).hexdigest(),
)
manifest.write_text(json.dumps(template))
"""


def artifact_project(root: Path):
    project = compliance_project(root)
    if run_compliance(project, compliance_response(project)) != 0:
        raise AssertionError("target compliance fixture did not finalize")
    return project


def write_plan(project, *, stale: bool = False) -> Path:
    acquisition = load_repository_acquisition(project)
    bundle = project.load_json_artifact(
        MigrationStage.DRIVER_IMPLEMENTATION,
        MigrationArtifact.IMPLEMENTATION_BUNDLE,
    )
    payloads = [
        {"path": item["path"], "role": item["role"], "sha256": item["sha256"]}
        for item in bundle["files"]
    ]
    files = {
        "artifact-builder.py": STALE_BUILDER if stale else BUILDER,
        "artifact-inspector.py": INSPECTOR,
        "base.img": "immutable target base\n",
        "payloads.json": json.dumps(
            {
                "implementation_bundle_sha256": project.artifact(
                    MigrationStage.DRIVER_IMPLEMENTATION,
                    MigrationArtifact.IMPLEMENTATION_BUNDLE,
                ).digest,
                "payloads": payloads,
            }
        ),
    }
    for relative, content in files.items():
        (project.root / relative).write_text(content, encoding="utf-8")
    worktree = project.root / acquisition.target_worktree.path
    output = project.root / "artifacts"
    mode = project.load_json_artifact(
        EnvironmentStage.RECOVERY,
        EnvironmentArtifact.MODE_RECORD,
    )["artifact_mode"]
    builder_args = [
        str(project.root / "base.img"),
        str(output / "final.img"),
        str(output / "public-tests.pkg"),
        str(worktree),
        str(project.root / "payloads.json"),
    ]
    inspector_args = [
        str(output / "final.img"),
        str(output / "public-tests.pkg"),
        str(worktree),
        str(project.root / "payloads.json"),
        str(output / "presence.json"),
    ]
    plan = {
        "schema_version": 1,
        "plan_id": "stale-copy" if stale else "current-driver",
        "artifact_mode": mode,
        "cwd": acquisition.target_worktree.path,
        "build": {
            "argv": [sys.executable, str(project.root / "artifact-builder.py"), *builder_args],
            "environment": {},
            "timeout_seconds": 10,
            "accepted_exit_codes": [0],
        },
        "inspect": {
            "argv": [sys.executable, str(project.root / "artifact-inspector.py"), *inspector_args],
            "environment": {},
            "timeout_seconds": 10,
            "accepted_exit_codes": [0],
        },
        "base_artifact": "base.img",
        "final_artifact": "artifacts/final.img",
        "packaged_test_artifact": "artifacts/public-tests.pkg",
        "presence_manifest": "artifacts/presence.json",
        "tool_evidence_paths": [
            "artifact-builder.py",
            "artifact-inspector.py",
            "payloads.json",
        ],
    }
    path = project.root / "artifact-plan.json"
    path.write_text(json.dumps(plan), encoding="utf-8")
    return path


class ArtifactPreparationTests(unittest.TestCase):
    def test_controlled_builder_binds_current_driver_presence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = artifact_project(Path(temporary))
            plan = write_plan(project)

            self.assertEqual(
                main(
                    [
                        "artifact-preparation",
                        "run",
                        str(project.root),
                        "--plan",
                        str(plan),
                    ]
                ),
                0,
            )
            self.assertEqual(
                project.stage(MigrationStage.ARTIFACT_PREPARATION).status,
                StageStatus.PASS,
            )
            identity = project.load_json_artifact(
                MigrationStage.ARTIFACT_PREPARATION,
                MigrationArtifact.ARTIFACT_IDENTITY,
            )
            self.assertEqual(identity["runtime_status"], "NOT_RUN")

    def test_stale_base_copy_cannot_finalize(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = artifact_project(Path(temporary))
            plan = write_plan(project, stale=True)

            self.assertEqual(
                main(
                    [
                        "artifact-preparation",
                        "run",
                        str(project.root),
                        "--plan",
                        str(plan),
                    ]
                ),
                0,
            )
            self.assertEqual(
                project.stage(MigrationStage.ARTIFACT_PREPARATION).status,
                StageStatus.RUNNING,
            )
            attempts = [
                ref
                for ref in project.artifact_refs(stage=MigrationStage.ARTIFACT_PREPARATION)
                if ref.kind == MigrationArtifact.ARTIFACT_PREPARATION_ATTEMPT.value
            ]
            self.assertEqual(len(attempts), 1)


if __name__ == "__main__":
    unittest.main()
