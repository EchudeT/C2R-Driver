"""One whole-driver package through real scheduling; synthetic worker/checks, no paid model."""

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from driver_port_factory.codex.check_mcp import dispatch
from driver_port_factory.codex.gateway import CodexResult
from driver_port_factory.core.models import StageStatus
from driver_port_factory.migration import behavior
from driver_port_factory.migration.check_tools import check
from driver_port_factory.migration.contracts import MigrationStage as S
from tests.migration_support import delivery_fixture
from tests.target_support import write_route_fixture
from tests.test_local_adaptation import ready
from tests.workflow_support import runner


def whole_driver_fixture(report):
    index = write_route_fixture(report)
    index["behaviors"] = [
        {
            "id": "driver",
            "section": "Whole driver",
            "route": ["R1"],
            "contracts": ["C1", "C2"],
            "depends_on": [],
        }
    ]
    index["contracts"].append(
        {
            "id": "C2",
            "section": "Cleanup obligation",
            "sources": index["contracts"][0]["sources"],
        }
    )
    report.write_text(
        report.read_text()
        + (
            "\n## Whole driver\n"
            "Initialize, expose the operation and clean up in one complete goal.\n"
            "## Cleanup obligation\nRelease synthetic state after use; no live target claims.\n"
        )
    )
    report.with_suffix(".route.json").write_text(json.dumps(index))
    return index


@pytest.mark.parametrize("continue_first", [False, True])
def test_complete_small_driver_stays_one_package_then_independent_delivery(
    tmp_path, continue_first
):
    with patch("tests.target_support.write_route_fixture", side_effect=whole_driver_fixture):
        project = ready(tmp_path)
    calls = []

    def worker(job):
        payload = json.loads(job.prompt.split("<job>")[1].split("</job>")[0])
        packet = payload["reference_material"]["behavior_progress"]
        current = packet["current"]
        calls.append(current["id"] if current else None)
        if len(calls) > 1:
            assert job.thread_id == "same-whole-driver-session"
        output = job.execution_root / ".dpf-output"
        output.mkdir(exist_ok=True)
        report = output / "report.md"
        report.write_text("Synthetic whole-driver implementation; not real validation.\n")
        if current:
            assert len(current["behaviors"]) == 1
            assert current["behaviors"][0]["contracts"] == ["C1", "C2"]
            assert {c["id"] for c in packet["route_context"]["contracts"]} == {"C1", "C2"}
            # Multiple coding/check actions belong to one worker call, not extra scheduled units.
            source = job.execution_root / "driver.rs"
            source.write_text(
                "pub fn initialize() {}\npub fn operation() {}\npub fn cleanup() {}\n"
            )
            delivery_fixture(job.execution_root, case_count=3)
            before = behavior._load(project)
            assert "cannot skip" in behavior.finish(
                project, S.DRIVER_IMPLEMENTATION, {"job_id": job.job_id, "decision": "pass"}
            )
            assert behavior._load(project) == before
            status = "continue" if continue_first and len(calls) == 1 else "done"
            assert packet["final_work_package"]
            if status == "done":
                assert "PASS" in check(project, job.job_id, {})
            dispatch(
                project,
                job.job_id,
                "progress",
                {"status": status, "note": "Synthetic source self-check; no real-driver claim."},
            )
            assert project.stage(S.DRIVER_IMPLEMENTATION).status is StageStatus.RUNNING
        else:
            pytest.fail("Last package must not create a report-only model round")
        return CodexResult(job.job_id, "", "same-whole-driver-session")

    with patch("driver_port_factory.codex.cli.CodexExecGateway.run", side_effect=worker):
        runner(project)._implementation(project)
    assert calls == (["driver", "driver"] if continue_first else ["driver"])
    assert project.stage(S.DRIVER_IMPLEMENTATION).status is StageStatus.PASS
    assert project.stage(S.ARTIFACT_PREPARATION).status is StageStatus.READY
    assert project.stage(S.PUBLIC_QEMU_VALIDATION).status is not StageStatus.PASS
    verify_final_batch(project)


def verify_final_batch(project):
    # Stage 15 must execute all three again as one final batch, with no extra model review.
    from driver_port_factory.acquisition.repository import load_repository_acquisition
    from driver_port_factory.migration.contracts import MigrationArtifact as A
    from driver_port_factory.migration.public_qemu import PublicQemuService, _run_public_harness

    with (
        patch(
            "driver_port_factory.codex.cli.CodexExecGateway.run",
            side_effect=AssertionError("extra model"),
        ),
        patch(
            "driver_port_factory.migration.public_qemu._run_public_harness",
            wraps=_run_public_harness,
        ) as execute,
    ):
        runner(project)._artifact_preparation(project)
        assert execute.call_count == 0
        project.start(S.PUBLIC_QEMU_VALIDATION)
        service = PublicQemuService()
        tree = project.root / load_repository_acquisition(project).target_worktree.path
        report = project.artifacts.path_for_digest(
            project.artifact(S.DRIVER_IMPLEMENTATION, A.COMPLIANCE_REPORT).digest
        )
        args = {"script_path": tree / ".dpf-output/public-qemu.sh", "work_report_path": report}
        first = service.run_script(project, **args)
        assert first["status"] == "PASS" and execute.call_count == 3
        batch = service._latest_attempt(project)[1]["run"]
        assert len(batch["cases"]) == 3
        assert all(c["status"] == "PASS" and not c["reused"] for c in batch["cases"])
        identities = [
            json.loads(Path(c["receipt"]).read_text())["identity"] for c in batch["cases"]
        ]
        assert all(
            i["source"] == identities[0]["source"] and i["runtime"] == identities[0]["runtime"]
            for i in identities
        )
        # Temporary PATH/log/report changes cannot schedule another final execution.
        (tree / ".dpf-output/qemu-runs/new-log.txt").write_text("later logging")
        note = tree / ".dpf-output/later-report.md"
        note.write_text("Later report, same final candidate")
        with patch.dict(
            "os.environ", {"PATH": "/different/model/session/tmp:" + os.environ["PATH"]}
        ):
            second = service.run_script(project, **{**args, "work_report_path": note})
        assert second == first and execute.call_count == 3
        runner(project)._public_qemu(project)
        assert execute.call_count == 3
    assert project.stage(S.PUBLIC_QEMU_VALIDATION).status is StageStatus.PASS
    project.verify_integrity()
