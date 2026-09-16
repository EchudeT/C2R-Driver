from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from driver_port_factory.core.models import (
    ArtifactDirection,
    StageStatus,
    WorkflowError,
)
from driver_port_factory.knowledge.bootstrap import KnowledgeBootstrapper
from driver_port_factory.knowledge.corpus import CorpusManifest
from driver_port_factory.knowledge.index import KnowledgeIndex, file_sha256
from driver_port_factory.source_analysis.closure import SourceClosureService
from driver_port_factory.source_analysis.compiler import GccCompatibleCommand
from driver_port_factory.source_analysis.contracts import (
    SourceAnalysisStage,
)
from driver_port_factory.source_analysis.corpus_revision import SourceCorpusRevision
from driver_port_factory.target_study.service import TargetStudyService
from tests.test_knowledge import prepare_project, probe_plan
from tests.test_target_study import target_study_inputs


def write_json(path: Path, value: dict) -> Path:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def ready_project(root: Path):
    project, checkouts = prepare_project(root)
    KnowledgeBootstrapper().bootstrap(project, probe_plan_path=probe_plan(project.root))
    target_inputs, _ = target_study_inputs(project.root, project, checkouts)
    TargetStudyService().validate(project, **target_inputs)
    return project, checkouts


def source_closure(project, checkouts) -> dict:
    compiler = shutil.which("clang")
    if not compiler:
        raise unittest.SkipTest("test requires a C compiler")
    version = subprocess.run(
        [compiler, "--version"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()[0]
    source_root = (project.root / checkouts["source"].checkout_path).resolve()

    def dependency(relative: str, role: str) -> dict[str, str]:
        return {
            "path": relative,
            "sha256": file_sha256(source_root / relative),
            "role": role,
        }

    common_arguments = [
        compiler,
        "-std=gnu11",
        "-DEXAMPLE_FEATURE=1",
        "-Idrivers",
        "-include",
        "include/example-config.h",
        "-fsyntax-only",
    ]
    target_triple = GccCompatibleCommand.effective_target_triple(
        [*common_arguments, "drivers/example.c"], source_root
    )
    target_abi = GccCompatibleCommand.abi_signature(
        [*common_arguments, "drivers/example.c"],
        source_root,
    )
    units = []
    for unit_id, relative in (
        ("example-driver", "drivers/example.c"),
        ("example-shared", "drivers/shared.c"),
    ):
        units.append(
            {
                "unit_id": unit_id,
                "source_path": relative,
                "sha256": file_sha256(source_root / relative),
                "compile_directory": str(source_root),
                "arguments": [*common_arguments, relative],
                "dependencies": [
                    dependency("drivers/example.h", "header"),
                    dependency("include/example-config.h", "configuration"),
                ],
            }
        )

    def covered(*paths: str) -> dict:
        return {"status": "COVERED", "paths": list(paths), "rationale": "required by source"}

    def not_applicable(reason: str) -> dict:
        return {"status": "NOT_APPLICABLE", "paths": [], "rationale": reason}

    return {
        "schema_version": 1,
        "source_root": str(source_root),
        "source_revision": checkouts["source"].resolved_commit,
        "closure_status": "CLOSED",
        "compiler": {
            "family": "gcc-compatible",
            "executable": compiler,
            "version": version,
            "target_triple": target_triple,
            "target_abi": target_abi,
            "language_mode": "gnu11",
        },
        "defines": ["EXAMPLE_FEATURE=1"],
        "include_paths": ["drivers"],
        "configuration_inputs": covered("include/example-config.h"),
        "generated_headers": not_applicable("fixture has no generated headers"),
        "selected_conditional_branches": ["EXAMPLE_FEATURE=1:selected"],
        "translation_units": units,
        "closure_categories": {
            "shared_cores": covered("drivers/shared.c"),
            "headers": covered("drivers/example.h"),
            "macros_configuration": covered("include/example-config.h"),
            "callbacks_function_pointers": not_applicable("fixture has no callbacks"),
            "registration_tables": covered("drivers/example.c"),
            "source_tests": covered("tests/example-driver-test.c"),
            "framework_contracts": not_applicable("fixture has no external framework source"),
        },
        "unresolved_dependencies": [],
    }


class SourceClosureTests(unittest.TestCase):
    def test_successor_corpus_cannot_omit_validated_closure_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project, checkouts = ready_project(Path(temporary))
            submission = write_json(
                project.root / "source-closure.json",
                source_closure(project, checkouts),
            )

            def omit_additions(
                _service: SourceClosureService,
                current_project,
                _source_files,
            ):
                corpus = CorpusManifest.current(current_project)
                status = KnowledgeIndex(current_project.root, corpus).build()
                return (
                    SourceCorpusRevision(corpus.digest, (), corpus.digest, status),
                    corpus,
                    (),
                )

            with (
                patch.object(SourceClosureService, "_extend_knowledge", new=omit_additions),
                self.assertRaisesRegex(WorkflowError, "exactly close"),
            ):
                SourceClosureService().validate(project, closure_path=submission)
            self.assertEqual(
                project.stage(SourceAnalysisStage.SOURCE_CLOSURE).status,
                StageStatus.RUNNING,
            )

    def test_corpus_revision_must_bind_the_acquisition_parent_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project, checkouts = ready_project(Path(temporary))
            submission = write_json(
                project.root / "source-closure.json",
                source_closure(project, checkouts),
            )
            original = SourceCorpusRevision.to_dict
            base_index = KnowledgeIndex.for_project(project)
            base_status = base_index.status()

            def wrong_parent(revision: SourceCorpusRevision) -> dict[str, object]:
                value = original(revision)
                value["parent_manifest_sha256"] = "0" * 64
                return value

            with (
                patch.object(SourceCorpusRevision, "to_dict", new=wrong_parent),
                self.assertRaisesRegex(WorkflowError, "acquisition manifest"),
            ):
                SourceClosureService().validate(project, closure_path=submission)
            self.assertEqual(
                project.stage(SourceAnalysisStage.SOURCE_CLOSURE).status,
                StageStatus.RUNNING,
            )
            self.assertEqual(base_index.status(), base_status)
            self.assertEqual(
                SourceClosureService().validate(project, closure_path=submission).status.value,
                "PASS",
            )

    def test_compiler_target_triple_and_abi_are_verified(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project, checkouts = ready_project(Path(temporary))
            closure = source_closure(project, checkouts)
            actual_triple = closure["compiler"]["target_triple"]
            closure["compiler"]["target_triple"] = "wrong-unknown-target"
            service = SourceClosureService()
            wrong_triple = service.validate(
                project,
                closure_path=write_json(project.root / "source-closure.json", closure),
            )
            self.assertEqual(wrong_triple.status.value, "FAIL")
            self.assertIn("target_triple", wrong_triple.errors[0])

            closure["compiler"]["target_triple"] = actual_triple
            closure["compiler"]["target_abi"]["pointer_width_bits"] += 8
            wrong_abi = service.validate(
                project,
                closure_path=write_json(project.root / "source-closure.json", closure),
            )
            self.assertEqual(wrong_abi.status.value, "FAIL")
            self.assertIn("target_abi", wrong_abi.errors[0])

    def test_shared_core_must_be_a_translation_unit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project, checkouts = ready_project(Path(temporary))
            closure = source_closure(project, checkouts)
            closure["translation_units"] = closure["translation_units"][:1]
            result = SourceClosureService().validate(
                project,
                closure_path=write_json(project.root / "source-closure.json", closure),
            )
            self.assertEqual(result.status.value, "FAIL")
            self.assertIn("shared core files", result.errors[0])

    def test_hash_and_omitted_dependency_fail_then_corrected_closure_passes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project, checkouts = ready_project(Path(temporary))
            closure = source_closure(project, checkouts)
            submission = project.root / "source-closure.json"
            service = SourceClosureService()

            actual_hash = closure["translation_units"][0]["sha256"]
            closure["translation_units"][0]["sha256"] = "0" * 64
            failed_hash = service.validate(
                project,
                closure_path=write_json(submission, closure),
            )
            self.assertEqual(failed_hash.status.value, "FAIL")
            self.assertIn("hash mismatch", failed_hash.errors[0])
            self.assertEqual(
                project.stage(SourceAnalysisStage.SOURCE_CLOSURE).status,
                StageStatus.RUNNING,
            )

            closure["translation_units"][0]["sha256"] = actual_hash
            header = closure["translation_units"][0]["dependencies"].pop(0)
            failed_dependency = service.validate(
                project,
                closure_path=write_json(submission, closure),
            )
            self.assertEqual(failed_dependency.status.value, "FAIL")
            self.assertIn("omits compiler-discovered dependencies", failed_dependency.errors[0])

            closure["translation_units"][0]["dependencies"].insert(0, header)
            chunks = KnowledgeIndex.for_project(project).chunks_path
            chunks.write_text("stale index contents\n", encoding="utf-8")
            passed = service.validate(
                project,
                closure_path=write_json(submission, closure),
            )
            self.assertEqual(passed.status.value, "PASS")
            self.assertEqual(
                project.stage(SourceAnalysisStage.SOURCE_CLOSURE).status,
                StageStatus.PASS,
            )
            self.assertEqual(
                project.stage(SourceAnalysisStage.STRUCTURED_C_ANALYSIS).status,
                StageStatus.READY,
            )
            self.assertEqual(KnowledgeIndex.for_project(project).status()["status"], "READY")
            manifest = KnowledgeIndex.for_project(project).load_manifest()
            controlled_paths = {record["path"] for record in manifest}
            self.assertIn(
                f"{checkouts['source'].checkout_path}/drivers/shared.c",
                controlled_paths,
            )
            output_kinds = {
                artifact.kind
                for artifact in project.artifact_refs(
                    stage=SourceAnalysisStage.SOURCE_CLOSURE,
                    direction=ArtifactDirection.OUTPUT,
                )
            }
            self.assertIn("source_closure_report", output_kinds)
            self.assertIn("compilation_database", output_kinds)

    def test_same_submission_can_retry_after_knowledge_rebuild_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project, checkouts = ready_project(Path(temporary))
            closure = source_closure(project, checkouts)
            submission = write_json(project.root / "source-closure.json", closure)
            service = SourceClosureService()
            original_build = KnowledgeIndex.build
            call_count = 0

            def fail_once(index: KnowledgeIndex, *args, **kwargs):
                nonlocal call_count
                call_count += 1
                if call_count == 1:
                    raise WorkflowError("injected knowledge rebuild failure")
                return original_build(index, *args, **kwargs)

            with patch.object(KnowledgeIndex, "build", fail_once):
                failed = service.validate(project, closure_path=submission)
                passed = service.validate(project, closure_path=submission)

            self.assertEqual(failed.status.value, "FAIL")
            self.assertEqual(passed.status.value, "PASS")
            self.assertNotEqual(Path(failed.report_path).parent, Path(passed.report_path).parent)


if __name__ == "__main__":
    unittest.main()
