"""Synthetic controller fixtures; these do not validate a real migrated driver."""
from driver_port_factory.acquisition.repository import load_repository_acquisition
from driver_port_factory.codex.contracts import CodexOutputError
from driver_port_factory.migration.artifact_preparation import ArtifactPreparationService
from driver_port_factory.migration.contracts import MigrationArtifact as A, MigrationStage as S
from driver_port_factory.migration.implementation import DriverImplementationService
from driver_port_factory.migration.public_qemu import PublicQemuService
from driver_port_factory.migration.public_repair import PublicRepairService
from tests.test_workflow_alignment import ready_implementation


def implemented(root, *, source="pub fn init() -> u32 { 1 }\n", request=""):
    project = ready_implementation(root)
    worktree = project.root / load_repository_acquisition(project).target_worktree.path
    (worktree / "driver.rs").write_text(source)
    output = worktree / ".dpf-output"
    output.mkdir()
    report = output / "report.md"
    report.write_text(f"# Synthetic fixture\n{request}\nDPF_SELF_REVIEW: PASS\n")
    project.start(S.DRIVER_IMPLEMENTATION)
    DriverImplementationService().snapshot_worktree(project, report)
    return project, worktree, report


def packaged(root, **implementation):
    project, worktree, report = implemented(root, **implementation)
    output = worktree / ".dpf-output"
    (output / "runtime-artifact").write_bytes((worktree / "driver.rs").read_bytes())
    (output / "check-presence.sh").write_text(
        'cmp "$DPF_RUNTIME_ARTIFACT" "$DPF_TARGET_WORKTREE/driver.rs"\n')
    project.start(S.ARTIFACT_PREPARATION)
    ArtifactPreparationService().capture_codex_artifact(project, report)
    return project, worktree, report


def public_run(root, *, exit_code=0, **implementation):
    project, worktree, report = packaged(root, **implementation)
    output = worktree / ".dpf-output"
    (output / "qemu-runs").mkdir()
    # Controller-only simulator; this fixture never establishes real driver behavior.
    qemu = output / "qemu-system-fixture"
    qemu.symlink_to("/bin/true")
    script = output / "public-qemu.sh"
    script.write_text(f'"{qemu}" -kernel "$DPF_RUNTIME_ARTIFACT"\n'
                      'printf "synthetic observation\\n" > .dpf-output/qemu-runs/serial.log\n'
                      f'exit {exit_code}\n')
    project.start(S.PUBLIC_QEMU_VALIDATION)
    report.write_text("# Harness ready\nDPF_RUN: PUBLIC_QEMU\n")
    if exit_code:
        try:
            PublicQemuService().run_script(project, script_path=script, work_report_path=report)
        except CodexOutputError as error:
            assert "Public harness failed" in str(error)
        else:
            raise AssertionError("failed execution advanced")
    else:
        PublicQemuService().run_script(project, script_path=script, work_report_path=report)
        report.write_text("# Captured synthetic run inspected\nDPF_SELF_REVIEW: PASS\n")
        PublicQemuService().accept_self_review(project, work_report_path=report)
    return project, worktree, report


def accepted(root):
    project, worktree, report = public_run(root)
    PublicRepairService().finalize(project)
    return project, worktree, report
