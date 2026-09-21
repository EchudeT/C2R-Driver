from __future__ import annotations


from pathlib import Path


from driver_port_factory.codex.contracts import CodexBackend


from driver_port_factory.core.models import FileArtifact


from driver_port_factory.migration.contracts import MigrationArtifact, MigrationStage


from driver_port_factory.port import PortOptions, PortRunner


from tests.design_support import ready_project


def ready_implementation(root: Path, *, plan=True):
    # This fixture validates the controller; its simulator is explicitly synthetic.
    project, checkouts = ready_project(root)
    skill = Path(project.config.skill_root) / "knowledge-guided-driver-port"
    skill.mkdir(exist_ok=True)
    (skill / "SKILL.md").write_text("# Fixture skill\nUse original evidence.\n")
    (skill / "references").mkdir(exist_ok=True)
    for name in ("translation.md", "knowledge-contract.md"):
        (skill / "references" / name).write_text("# Evidence fixture\n")
    if not plan:
        return project
    project.start(MigrationStage.CONTRACTS)
    report = project.root / "migration-plan.md"
    report.write_text(
        "# Fixture plan\nPreserve example_init returning shared_value.\n"
        "Test with one operation and a wrong-device control.\nDPF_SELF_REVIEW: PASS\n"
    )
    project.finalize_stage(
        MigrationStage.CONTRACTS,
        (
            FileArtifact(MigrationArtifact.CONTRACTS, report),
            FileArtifact(MigrationArtifact.TEST_PORT_MATRIX, report),
        ),
    )
    references = Path(project.config.skill_root) / "knowledge-guided-driver-port/references"
    for name in ("workflow.md", "test-porting.md", "target-changes.md", "qemu-evidence.md"):
        (references / name).write_text("# Fixture rule\n" + "Inspect original evidence.\n" * 80)
    return project


def runner(project):
    return PortRunner(
        PortOptions(
            workspace=project.root,
            source_platform="example-source",
            target_platform="example-target",
            driver_name="example-driver",
            skill_root=Path(project.config.skill_root),
            catalogs=(),
            backend=CodexBackend.EXEC,
            codex_bin="codex",
            model=None,
        )
    )
