from __future__ import annotations

import json
import shutil
import subprocess
import unittest
from pathlib import Path

from driver_port_factory.core.models import (
    StageStatus,
)
from driver_port_factory.knowledge.bootstrap import KnowledgeBootstrapper
from driver_port_factory.knowledge.index import KnowledgeIndex, file_sha256
from driver_port_factory.migration.handoff import MigrationHandoff
from driver_port_factory.source_analysis.closure import SourceClosureService
from driver_port_factory.source_analysis.compiler import GccCompatibleCommand
from driver_port_factory.source_analysis.contracts import (
    SourceAnalysisStage,
)
from tests.test_knowledge import prepare_project
from tests.test_target_study import accept_target_study


def write_json(path: Path, value: dict) -> Path:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def ready_project(root: Path):
    project, checkouts = prepare_project(root)
    KnowledgeBootstrapper().build_infrastructure(project)
    accept_target_study(project)
    MigrationHandoff().create(project)
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


def prepare_source(project, closure):
    database = project.root / "compile_commands.json"
    database.write_text(json.dumps([{
        "directory": u["compile_directory"],
        "file": str(Path(closure["source_root"]) / u["source_path"]),
        "arguments": u["arguments"],
    } for u in closure["translation_units"]]))
    report = project.root / "source-report.md"
    report.write_text("# Source inputs\nDPF_RUN: SOURCE_ANALYSIS\n")
    return SourceClosureService().prepare_compilation_database(
        project, compilation_database_path=database, work_report_path=report)


def finish_source(project):
    from driver_port_factory.source_analysis.preparation import finish
    report = project.root / "source-report.md"
    report.write_text("# Source coverage fixture\nDPF_SELF_REVIEW: PASS\n")
    finish(project, report)


def test_compilation_receipt_does_not_finish_source_analysis(tmp_path):
    project, checkouts = ready_project(tmp_path)
    prepare_source(project, source_closure(project, checkouts))
    assert project.stage(SourceAnalysisStage.SOURCE_CLOSURE).status is StageStatus.RUNNING
    from driver_port_factory.source_analysis.preparation import artifact
    from driver_port_factory.source_analysis.contracts import SourceAnalysisArtifact as A
    manifest = json.loads(project.artifacts.read(artifact(project, A.COMPILE_MANIFEST)))
    assert len(manifest["translation_units"]) == 2
    assert KnowledgeIndex.for_project(project).status()["status"] == "READY"
