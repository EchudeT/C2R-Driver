import json
import pytest
from driver_port_factory.core.models import FileArtifact, WorkflowError
from driver_port_factory.migration.contracts import MigrationArtifact as A, MigrationStage as S
from tests.migration_support import implemented

def test_snapshot_records_files_not_model_written_content(tmp_path):
    p, w, _ = implemented(tmp_path)
    bundle = p.load_json_artifact(S.DRIVER_IMPLEMENTATION, A.IMPLEMENTATION_BUNDLE)
    assert [f["path"] for f in bundle["files"]] == ["driver.rs"]
    assert all("content" not in f for f in bundle["files"])
    p.verify_integrity()

@pytest.mark.parametrize("mutation", ["content", "new_file", "deleted"])
def test_snapshot_revalidation_rejects_drift(tmp_path, mutation):
    p, w, _ = implemented(tmp_path)
    outputs = [FileArtifact(kind, p.artifacts.path_for_digest(p.artifact(S.DRIVER_IMPLEMENTATION, kind).digest))
               for kind in (A.IMPLEMENTATION_BUNDLE, A.TRANSLATION_COVERAGE,
                            A.TARGET_CHANGE_INVENTORY, A.COMPLIANCE_REPORT)]
    p.start(S.ARTIFACT_PREPARATION)
    p.retry_from(S.DRIVER_IMPLEMENTATION, trigger=S.ARTIFACT_PREPARATION, reason="fixture")
    p.start(S.DRIVER_IMPLEMENTATION)
    if mutation == "content": (w / "driver.rs").write_text("changed")
    elif mutation == "new_file": (w / "extra.rs").write_text("new source")
    else: (w / "driver.rs").unlink()
    with pytest.raises(WorkflowError):
        p.finalize_stage(S.DRIVER_IMPLEMENTATION, outputs)
