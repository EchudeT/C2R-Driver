import pytest
from driver_port_factory.codex.contracts import CodexOutputError
from driver_port_factory.core.models import StageStatus
from driver_port_factory.migration.artifact_preparation import ArtifactPreparationService
from driver_port_factory.migration.implementation import ImplementationChanged
from driver_port_factory.migration.contracts import MigrationArtifact as A, MigrationStage as S
from tests.migration_support import implemented, packaged

def test_real_checker_binds_current_artifact(tmp_path):
    p, _, _ = packaged(tmp_path)
    identity = p.load_json_artifact(S.ARTIFACT_PREPARATION, A.ARTIFACT_IDENTITY)
    assert identity["runtime_artifact"]["sha256"] == p.artifact(S.ARTIFACT_PREPARATION, A.RUNTIME_ARTIFACT).digest
    p.verify_integrity()

@pytest.mark.parametrize("defect", ["stale_payload", "source_drift", "new_source"])
def test_checker_and_review_boundary_reject_invalid_artifact(tmp_path, defect):
    p, w, report = implemented(tmp_path)
    output = w / ".dpf-output"
    (output / "runtime-artifact").write_text("stale payload")
    (output / "check-presence.sh").write_text('cmp "$DPF_RUNTIME_ARTIFACT" "$DPF_TARGET_WORKTREE/driver.rs"\n')
    p.start(S.ARTIFACT_PREPARATION)
    if defect == "source_drift": (w / "driver.rs").write_text("changed")
    if defect == "new_source": (w / "new.rs").write_text("new source")
    error = CodexOutputError if defect == "stale_payload" else ImplementationChanged
    with pytest.raises(error):
        ArtifactPreparationService().capture_codex_artifact(p, report)
    assert p.stage(S.ARTIFACT_PREPARATION).status is StageStatus.RUNNING
