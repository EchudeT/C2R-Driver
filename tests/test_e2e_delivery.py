"""Current controller policy, with synthetic sources and execution, never driver certification."""

import json


from unittest.mock import patch


import pytest


from driver_port_factory.codex.gateway import CodexResult


from driver_port_factory.composition import open_project


from driver_port_factory.migration.contracts import MigrationArtifact as A, MigrationStage as S


from tests.workflow_support import ready_implementation, runner


@pytest.mark.parametrize("risk", [False, True])
def test_worker_to_completion_has_final_authority_over_risk_checker(tmp_path, risk):
    project = ready_implementation(tmp_path)
    port = runner(project)
    calls = []

    def gateway(job):
        calls.append(job)
        stage = job.stage
        output = job.execution_root / ".dpf-output"
        output.mkdir(exist_ok=True)
        report = output / "report.md"
        text = "Synthetic controller fixture, not real driver evidence.\n"
        if stage is S.DRIVER_IMPLEMENTATION:
            (job.execution_root / "driver.rs").write_text(
                "pub unsafe fn init() {}\n" if risk else "pub fn init() {}\n"
            )
            # Ordinary preexisting integration changes do not create a second audit.
            integration = job.execution_root / "src/driver-api.rs"
            integration.write_text(integration.read_text() + "pub fn register_driver() {}\n")
        elif stage is S.ARTIFACT_PREPARATION:
            (output / "runtime-artifact").write_bytes(
                (job.execution_root / "driver.rs").read_bytes()
            )
            (output / "check-presence.sh").write_text(
                'cmp "$DPF_RUNTIME_ARTIFACT" "$DPF_TARGET_WORKTREE/driver.rs"\n'
            )
        elif stage is S.PUBLIC_QEMU_VALIDATION:
            ctx = json.loads(job.prompt.split("<job>")[1].split("</job>")[0])["reference_material"]
            (output / "qemu-runs").mkdir(exist_ok=True)
            qemu = output / "qemu-system-fixture"
            if not qemu.exists():
                qemu.symlink_to("/bin/true")
            (output / "public-qemu.sh").write_text(
                f'"{qemu}" -kernel "$DPF_RUNTIME_ARTIFACT"\n'
                "echo synthetic > .dpf-output/qemu-runs/serial.log\n"
            )
        else:
            raise AssertionError(f"unexpected paid stage: {stage}")
        if stage is S.PUBLIC_QEMU_VALIDATION and "controller_execution" not in ctx:
            text += "DPF_RUN: PUBLIC_QEMU\n"
        elif stage in (S.DRIVER_IMPLEMENTATION, S.PUBLIC_QEMU_VALIDATION):
            text += "DPF_SELF_REVIEW: PASS\n"
        report.write_text(text)
        thread = "worker"
        assert job.thread_id in (None, thread)
        return CodexResult(job.job_id, f"REPORT_PATH: {report}\n", thread)

    with patch("driver_port_factory.codex.cli.CodexExecGateway.run", side_effect=gateway):
        # Use the real controller loop, stage finalizers, script execution, CAS and ledger.
        outcome = port._run_project(project)
    assert outcome.status.value == "PASS"
    assert [job.stage for job in calls].count(S.DRIVER_IMPLEMENTATION) == 1
    assert S.PUBLIC_REPAIR.value not in project.workflow.stage_values
    worker = [job for job in calls if job.stage is not S.PUBLIC_REPAIR]
    assert len(worker) == 4
    assert worker[0].thread_id is None
    assert all(job.thread_id == "worker" for job in worker[1:])
    reopened = open_project(project.root)
    audit = reopened.load_json_artifact(S.COMPLETION_AUDIT, A.EVIDENCE_AUDIT)
    assert audit["work_products"]["review_mode"] == "worker_self_check"
    assert not audit["worker_acceptances"]
    assert audit["failure_attribution"] == []
    assert audit["scope_limits"]["real_hardware"] == "NOT_RUN"
    reopened.verify_integrity()
