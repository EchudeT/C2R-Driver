from typing import Any

from ..core.models import WorkflowError
from ..core.project import Project
from .frozen_checkout_validation import verify_git_checkout, verify_lock
from .repository import load_repository_acquisition


class AcquisitionVerifier:
    def verify(self, project: Project) -> dict[str, Any]:
        acquisition = load_repository_acquisition(project)
        results = []
        for record in acquisition.checkouts:
            try:
                verify_lock(project.root, record)
                verify_git_checkout(project.root, record)
            except WorkflowError as error:
                results.append({"role": record.role.value, "valid": False, "failure": str(error)})
            else:
                results.append({"role": record.role.value, "valid": True})
        return {
            "project_id": project.config.project_id,
            "repositories": results,
            "valid": all(result["valid"] for result in results),
        }
