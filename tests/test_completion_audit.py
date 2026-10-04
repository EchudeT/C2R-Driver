"""Real controller artifacts with synthetic execution; not driver or blind-evaluation evidence."""

from dataclasses import replace
from unittest.mock import patch

import pytest

from driver_port_factory.composition import open_project
from driver_port_factory.core.models import EvaluationMode, WorkflowError
from driver_port_factory.core.validation import BundleValidationContext
from driver_port_factory.migration.completion_audit import (
    CompletionAuditService,
    validate_completion_audit_bundle,
)
from driver_port_factory.migration.contracts import MigrationArtifact as A
from driver_port_factory.migration.contracts import MigrationStage as S
from driver_port_factory.orchestration.migration import migration_workflow
from tests.migration_support import accepted
from tests.test_optional_reviews import config


def test_completion_audit_uses_collected_runtime_and_independent_review(tmp_path):
    # Exercise the real finalizer/bundle validator without claiming a sealed fixture is blind.
    blind = migration_workflow(config(mode=EvaluationMode.POST_HOC_SEALED_BLIND))
    audit_spec = next(s for s in blind if s.name is S.COMPLETION_AUDIT)
    required = (S.FINAL_EVIDENCE_REVIEW, S.ARTIFACT_PREPARATION, S.PUBLIC_QEMU_VALIDATION)
    assert all(s in audit_spec.dependencies for s in required)

    def fixture_workflow(settings):
        return (*migration_workflow(settings), replace(audit_spec, dependencies=required))

    with patch("driver_port_factory.composition.migration_workflow", fixture_workflow):
        project, _, _ = accepted(tmp_path)
        result = CompletionAuditService().run(project)
        assert result["public_runs"][0]["attribution"] == "PUBLIC_HARNESS"
        assert result["scope_limits"]["qemu_evidence"] == "CURRENT_RUNTIME_ON_QEMU_PUBLIC_HARNESS"
        assert result["work_products"]["review_decision"] == "PASS"
        assert result["scope_limits"]["semantic_coverage"] == (
            "INDEPENDENT_REVIEW_RECORDED_NOT_EXHAUSTIVE_OR_BLIND_VERIFICATION"
        )
        assert result["unresolved"] == []
        open_project(project.root).verify_integrity()
        # Direct bundle submission must not misrepresent review or promote harness to semantics.
        import json
        from copy import deepcopy

        dependencies = tuple(
            (ref, project.artifacts.read(ref))
            for stage in required
            for ref in project.artifact_refs(stage=stage)
        )
        for section, key, value in (
            ("work_products", "review_decision", None),
            ("scope_limits", "qemu_evidence", "TARGET_DRIVER_ON_QEMU"),
        ):
            forged = deepcopy(result)
            forged[section][key] = value
            data = json.dumps(forged).encode()
            ref = project.artifact(S.COMPLETION_AUDIT, A.EVIDENCE_AUDIT)
            with pytest.raises(WorkflowError, match="misstates"):
                validate_completion_audit_bundle(
                    BundleValidationContext(project.root, ((ref, data),), dependencies)
                )
