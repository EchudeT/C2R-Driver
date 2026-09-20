from driver_port_factory.target_study.service import TargetStudyService
from driver_port_factory.target_study.contracts import TargetStudyStage
from driver_port_factory.core.models import StageStatus
from driver_port_factory.knowledge.bootstrap import KnowledgeBootstrapper
from tests.test_knowledge import prepare_project


def accept_target_study(project):
    project.start(TargetStudyStage.STUDY)
    report = project.root / "target-profile.md"
    report.write_text("# Fixture target study\nInspect pinned registration, ownership and packaging originals.\n")
    TargetStudyService().accept(project, report)


def test_natural_report_is_the_only_target_study_submission(tmp_path):
    project, _ = prepare_project(tmp_path)
    KnowledgeBootstrapper().build_infrastructure(project)
    accept_target_study(project)
    assert project.stage(TargetStudyStage.STUDY).status is StageStatus.PASS
    refs = project.current_artifact_refs(stage=TargetStudyStage.STUDY)
    assert len({r.digest for r in refs}) == 1
