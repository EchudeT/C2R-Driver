from driver_port_factory.target_study.service import TargetStudyService


from driver_port_factory.target_study.contracts import TargetStudyStage


def accept_target_study(project):
    project.start(TargetStudyStage.STUDY)
    report = project.root / "target-profile.md"
    report.write_text(
        "# Fixture target study\nInspect pinned registration, ownership and packaging originals.\n"
    )
    TargetStudyService().accept(project, report)
