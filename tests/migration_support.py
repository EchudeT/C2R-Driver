"""Synthetic controller fixtures; these do not validate a real migrated driver."""

from driver_port_factory.acquisition.repository import load_repository_acquisition
from driver_port_factory.migration.artifact_preparation import ArtifactPreparationService
from driver_port_factory.migration.contracts import MigrationStage as S
from driver_port_factory.migration.implementation import DriverImplementationService
from driver_port_factory.migration.public_qemu import PublicQemuService
from tests.workflow_support import ready_implementation


def implemented(root, *, source="pub fn init() -> u32 { 1 }\n", request=""):
    project = ready_implementation(root)
    worktree = project.root / load_repository_acquisition(project).target_worktree.path
    (worktree / "driver.rs").write_text(source)
    output = worktree / ".dpf-output"
    output.mkdir()
    report = output / "report.md"
    report.write_text(f"# Synthetic fixture\n{request}\n")
    project.start(S.DRIVER_IMPLEMENTATION)
    DriverImplementationService().snapshot_worktree(project, report)
    return project, worktree, report


def packaged(root, **implementation):
    project, worktree, report = implemented(root, **implementation)
    output = worktree / ".dpf-output"
    (output / "runtime-artifact").write_bytes((worktree / "driver.rs").read_bytes())
    (output / "check-presence.sh").write_text(
        'cmp "$DPF_RUNTIME_ARTIFACT" "$DPF_TARGET_WORKTREE/driver.rs"\n'
    )
    project.start(S.ARTIFACT_PREPARATION)
    ArtifactPreparationService().capture_codex_artifact(project, report)
    return project, worktree, report


def public_run(root, *, exit_code=0, self_check=True, **implementation):
    project, worktree, report = packaged(root, **implementation)
    output = worktree / ".dpf-output"
    (output / "qemu-runs").mkdir()
    # Controller-only simulator; this fixture never establishes real driver behavior.
    qemu = output / "qemu-system-fixture"
    qemu.symlink_to("/bin/true")
    script = output / "public-qemu.sh"
    script.write_text(
        f'"{qemu}" -kernel "$DPF_RUNTIME_ARTIFACT"\n'
        'printf "synthetic observation\\n" > .dpf-output/qemu-runs/serial.log\n'
        f"exit {exit_code}\n"
    )
    project.start(S.PUBLIC_QEMU_VALIDATION)
    report.write_text("# Harness ready\n")
    if exit_code:
        result = PublicQemuService().run_script(
            project, script_path=script, work_report_path=report
        )
        assert result["status"] == "FAIL"
        assert project.stage(S.PUBLIC_QEMU_VALIDATION).status.value == "RUNNING"
    else:
        PublicQemuService().run_script(project, script_path=script, work_report_path=report)
        report.write_text("# Captured synthetic run inspected\n")
        if self_check:
            PublicQemuService().accept_self_review(project, work_report_path=report)
    return project, worktree, report


def accepted(root):
    project, worktree, report = public_run(root)
    if S.PUBLIC_REPAIR.value in project.workflow.stage_values:
        from driver_port_factory.migration.public_repair import PublicRepairService

        report.write_text("Synthetic independent fixture review.\n")
        PublicRepairService().finalize(project, review_path=report)
    return project, worktree, report
