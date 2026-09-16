from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from driver_port_factory.acquisition.repository_checkout import CheckoutRecord
from driver_port_factory.core.models import StageStatus
from driver_port_factory.environment.contracts import EnvironmentArtifact, EnvironmentStage
from driver_port_factory.knowledge.bootstrap import KnowledgeBootstrapper
from driver_port_factory.knowledge.contracts import KnowledgeDomain
from driver_port_factory.knowledge.index import KnowledgeIndex
from driver_port_factory.migration.contracts import MigrationStage
from driver_port_factory.target_study.contracts import TargetStudyArtifact, TargetStudyStage
from driver_port_factory.target_study.service import (
    PROFILE_HEADINGS,
    TRACE_STEPS,
    TargetStudyService,
)
from tests.test_knowledge import prepare_project, probe_plan


def write_json(path: Path, value: dict) -> Path:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def target_study_inputs(
    root: Path,
    project,
    checkouts: dict[str, CheckoutRecord],
) -> tuple[dict[str, Path], dict[str, str]]:
    index = KnowledgeIndex.for_project(project)
    target_hit = index.search("registration lifecycle", domain=KnowledgeDomain.TARGET)["results"][0]
    source_hit = index.search("example driver source entry", domain=KnowledgeDomain.SOURCE)[
        "results"
    ][0]
    target_ref = {
        "chunk_id": target_hit["chunk_id"],
        "record_id": target_hit["record_id"],
    }
    source_ref = {
        "chunk_id": source_hit["chunk_id"],
        "record_id": source_hit["record_id"],
    }
    target = checkouts["target"]
    artifact_mode = project.load_json_artifact(
        EnvironmentStage.RECOVERY, EnvironmentArtifact.MODE_RECORD
    )["artifact_mode"]
    section = {
        "summary": "Verified from the pinned target source and its in-tree driver contract.",
        "evidence_refs": [target_ref],
    }
    profile = {
        "schema_version": 1,
        "target_platform": project.config.target_platform,
        "source_root": str((project.root / target.checkout_path).resolve()),
        "revision": target.resolved_commit,
        "artifact_mode": artifact_mode,
        "profile_status": "READY",
        "unmodified_target_baseline": {
            "revision": target.resolved_commit,
            "tree_id": target.tree_id,
            "clean": True,
            "baseline_status": "NOT_RUN",
            "baseline_evidence": (
                "The frozen environment route is currently model-only; target baseline remains "
                "explicitly NOT_RUN."
            ),
        },
        **{name: section for name in PROFILE_HEADINGS_TO_STRUCTURED},
    }
    markdown = "\n\n".join(PROFILE_HEADINGS) + "\n\n"
    markdown += (
        f"- Target platform: {profile['target_platform']}\n"
        f"- Source root: {profile['source_root']}\n"
        f"- Revision: {profile['revision']}\n"
        f"- Artifact mode: {profile['artifact_mode']}\n"
        "- Profile status: `READY`\n"
    )
    api = {
        "schema_version": 1,
        "entries": [
            {
                "api_id": "target-api-001",
                "api_or_type": "TargetDriverApi",
                "purpose_in_driver": "register and operate the device",
                "definition_evidence": target_ref,
                "call_site_evidence": target_ref,
                "analogous_driver_evidence": target_ref,
                "documented_contract": "registration and lifecycle contract",
                "execution_context": "framework callback context",
                "ownership_lifetime_cleanup": "owned until cleanup",
                "error_behavior": "errors propagate without panic",
                "safety_or_unsafe_obligations": "safe wrapper boundary is preserved",
                "confidence": "VERIFIED",
            }
        ],
    }
    trace = {
        "schema_version": 1,
        "selected_implementation": "docs/driver-contract.md example driver",
        "selection_rationale": "same example bus and target framework",
        "framework_owners": ["TargetDriverApi"],
        "differences_not_to_copy": ["device-specific register behavior"],
        "steps": [
            {
                "stage": stage,
                "status": "VERIFIED",
                "summary": f"Verified target analogous path for {stage}",
                "evidence_refs": [target_ref],
            }
            for stage in TRACE_STEPS
        ],
    }
    changes = {
        "schema_version": 1,
        "integration_path": "new driver-owned module through the existing extension point",
        "required_change_level": "driver-owned",
        "driver_owned_paths": ["kernel/src/driver/example"],
        "proposed_preexisting_changes": [],
        "investigations": [],
    }
    paths = {
        "profile_json": write_json(root / "target-profile.json", profile),
        "profile_markdown": root / "target-profile.md",
        "api_table": write_json(root / "target-api.json", api),
        "analogous_trace": write_json(root / "analogous-trace.json", trace),
        "change_plan": write_json(root / "target-changes.json", changes),
    }
    paths["profile_markdown"].write_text(markdown, encoding="utf-8")
    return paths, {
        "target": target_ref["chunk_id"],
        "target_record": target_ref["record_id"],
        "source": source_ref["chunk_id"],
        "source_record": source_ref["record_id"],
    }


PROFILE_HEADINGS_TO_STRUCTURED = (
    "repository_documentation_map",
    "lifecycle_execution_contexts",
    "hardware_access_concurrency",
    "error_recovery_observability",
    "coding_safety_requirements",
    "artifact_qemu_path",
)


class TargetStudyTests(unittest.TestCase):
    def test_non_target_evidence_is_rejected_then_corrected_submission_passes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project, checkouts = prepare_project(Path(temporary))
            KnowledgeBootstrapper().bootstrap(project, probe_plan_path=probe_plan(project.root))
            paths, chunks = target_study_inputs(project.root, project, checkouts)
            api = json.loads(paths["api_table"].read_text(encoding="utf-8"))
            api["entries"][0]["definition_evidence"] = {
                "chunk_id": chunks["source"],
                "record_id": chunks["source_record"],
            }
            write_json(paths["api_table"], api)
            service = TargetStudyService()
            failed = service.validate(project, **paths)
            self.assertEqual(failed.status, "FAIL")
            self.assertIn("not in the target domain", failed.errors[0])
            self.assertEqual(
                project.stage(TargetStudyStage.STUDY).status,
                StageStatus.RUNNING,
            )

            api["entries"][0]["definition_evidence"] = {
                "chunk_id": chunks["target"],
                "record_id": chunks["target_record"],
            }
            write_json(paths["api_table"], api)
            passed = service.validate(project, **paths)
            self.assertEqual(passed.status, "PASS")
            self.assertEqual(
                project.stage(TargetStudyStage.STUDY).status,
                StageStatus.PASS,
            )
            self.assertEqual(
                project.stage(MigrationStage.HANDOFF).status,
                StageStatus.READY,
            )
            report = project.load_json_artifact(TargetStudyStage.STUDY, TargetStudyArtifact.REPORT)
            self.assertEqual(report["status"], "PASS")
            self.assertEqual(report["details"]["api_table"]["entry_count"], 1)


if __name__ == "__main__":
    unittest.main()
