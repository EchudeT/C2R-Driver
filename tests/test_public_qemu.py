import pytest
from driver_port_factory.core.models import WorkflowError
from driver_port_factory.migration.contracts import MigrationArtifact as A, MigrationStage as S
from driver_port_factory.migration.public_qemu import PublicQemuService
from tests.migration_support import packaged, public_run
from unittest.mock import patch

@pytest.mark.parametrize("exit_code,status", [(0, "PASS"), (1, "FAIL")])
def test_execution_result_is_captured_not_invented(tmp_path, exit_code, status):
    p, _, _ = public_run(tmp_path, exit_code=exit_code)
    kind = A.PUBLIC_QEMU_ATTEMPT if exit_code else A.PUBLIC_QEMU_REPORT
    report = p.load_json_artifact(S.PUBLIC_QEMU_VALIDATION, kind)
    assert report["execution_status"] == status
    assert p.stage(S.PUBLIC_QEMU_VALIDATION).status.value == ("RUNNING" if exit_code else "PASS")
    assert p.stage(S.PUBLIC_REPAIR).status.value == ("PENDING" if exit_code else "READY")
    p.verify_integrity()

def test_marker_without_qemu_is_not_driver_evidence(tmp_path):
    p, w, report = packaged(tmp_path)
    script = w / ".dpf-output/public-qemu.sh"
    script.write_text("echo PASS\n")
    p.start(S.PUBLIC_QEMU_VALIDATION)
    report.write_text("# Harness ready\nDPF_RUN: PUBLIC_QEMU\n")
    with pytest.raises(WorkflowError, match="collector/harness boundary"):
        PublicQemuService().run_script(p, script_path=script, work_report_path=report)


def test_receipt_reused_for_self_check_but_not_changed_helpers(tmp_path):
    p, w, report = packaged(tmp_path)
    out = w / ".dpf-output"
    (out / "harness").mkdir()
    helper = out / "harness/oracle.txt"
    helper.write_text("expected result\n")
    (out / "qemu-runs").mkdir()
    qemu = out / "qemu-system-fixture"
    qemu.symlink_to("/bin/true")
    script = out / "public-qemu.sh"
    script.write_text(f'"{qemu}" -kernel "$DPF_RUNTIME_ARTIFACT"\n'
                      'echo synthetic > .dpf-output/qemu-runs/serial.log\n')
    report.write_text("# Ready\nDPF_RUN: PUBLIC_QEMU\n")
    p.start(S.PUBLIC_QEMU_VALIDATION)
    service = PublicQemuService()
    first = service.run_script(p, script_path=script, work_report_path=report)
    assert p.stage(S.PUBLIC_QEMU_VALIDATION).status.value == "RUNNING"
    with patch("driver_port_factory.migration.public_qemu.run_public_harness",
               side_effect=AssertionError("unchanged receipt must not rerun QEMU")):
        assert service.run_script(p, script_path=script, work_report_path=report) == first
        report.write_text("# Actual captured observations inspected\nDPF_SELF_REVIEW: PASS\n")
        helper.write_text("different oracle\n")
        with pytest.raises(WorkflowError, match="inputs changed"):
            service.accept_self_review(p, work_report_path=report)
        helper.write_text("expected result\n")
        original_mode = helper.stat().st_mode & 0o7777
        helper.chmod(original_mode ^ 0o111)
        with pytest.raises(WorkflowError, match="inputs changed"):
            service.accept_self_review(p, work_report_path=report)
        helper.chmod(original_mode)
        service.accept_self_review(p, work_report_path=report)
    assert p.stage(S.PUBLIC_QEMU_VALIDATION).status.value == "PASS"
    p.verify_integrity()
