from typing import Any

from ..core.project import Project
from .contracts import AcquisitionArtifact, AcquisitionStage
from .git import GitAcquirer
from .models import CheckoutRecord


class AcquisitionVerifier:
    def verify(self, project: Project) -> dict[str, Any]:
        manifest = project.load_json_artifact(
            AcquisitionStage.EVIDENCE_ACQUISITION,
            AcquisitionArtifact.ACQUISITION_MANIFEST,
        )
        git = GitAcquirer(project.root, project.control)
        results = [git.verify(CheckoutRecord.from_dict(record)) for record in manifest["checkouts"]]
        return {
            "project_id": project.config.project_id,
            "repositories": results,
            "valid": all(result["valid"] for result in results),
        }
