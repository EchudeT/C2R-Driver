from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import patch

from driver_port_factory.acquisition.repository import load_repository_acquisition
from driver_port_factory.codex.contracts import CodexBackend, CodexOutputError, CodexSandbox
from driver_port_factory.codex.gateway import CodexExecGateway, CodexJob, CodexResult
from driver_port_factory.codex.policy import CodexExecutionPolicy
from driver_port_factory.codex.sessions import session_key, save_session, stage_session
from driver_port_factory.environment.contracts import EnvironmentStage
from driver_port_factory.target_study.contracts import TargetStudyStage
from driver_port_factory.acquisition.contracts import AcquisitionStage
from driver_port_factory.core.models import ActorRole, FileArtifact, StageStatus, WorkflowError
from driver_port_factory.migration.artifact_preparation import ArtifactPreparationService
from driver_port_factory.migration.contracts import MigrationArtifact, MigrationStage
from driver_port_factory.migration.implementation import DriverImplementationService
from driver_port_factory.migration.public_qemu import PublicQemuService
from driver_port_factory.port import PortOptions, PortRunner
from driver_port_factory.source_analysis.clang_backend import AnalyzerFamily
from driver_port_factory.source_analysis.contracts import SourceAnalysisStage
from driver_port_factory.source_analysis.closure import SourceClosureService
from driver_port_factory.source_analysis.query import query_facts
from driver_port_factory.source_analysis.navigation import cache_root, prepare_navigation
from driver_port_factory.source_analysis.structured import StructuredCAnalysisService
from tests.test_source_closure import ready_project, source_closure


def ready_implementation(root: Path, *, plan=True):
    # This fixture validates the controller; its simulator is explicitly synthetic.
    project, checkouts = ready_project(root)
    closure = source_closure(project, checkouts)
    database = project.root / "compile_commands.json"
    database.write_text(
        json.dumps(
            [
                {
                    "file": str(Path(closure["source_root"]) / unit["source_path"]),
                    "directory": unit["compile_directory"],
                    "arguments": unit["arguments"],
                }
                for unit in closure["translation_units"]
            ]
        )
    )
    report = project.root / "source-report.md"
    report.write_text(
        "# Source closure\nCompiler-derived fixture closure; no JSON report schema.\n"
    )
    SourceClosureService().prepare_compilation_database(
        project, compilation_database_path=database, work_report_path=report
    )
    result = StructuredCAnalysisService().analyze(project)
    if result.errors:
        raise AssertionError(result.errors)
    prepare_navigation(project)
    from driver_port_factory.source_analysis.preparation import finish
    report.write_text(report.read_text() + "\nDPF_SELF_REVIEW: PASS\n")
    finish(project, report)
    skill = Path(project.config.skill_root) / "knowledge-guided-driver-port"
    skill.mkdir(exist_ok=True)
    (skill / "SKILL.md").write_text("# Fixture skill\nUse original evidence.\n")
    (skill / "references").mkdir(exist_ok=True)
    for name in ("translation.md", "knowledge-contract.md"):
        (skill / "references" / name).write_text("# Evidence fixture\n")
    if not plan:
        return project
    project.start(MigrationStage.CONTRACTS)
    report = project.root / "migration-plan.md"
    report.write_text("# Fixture plan\nPreserve example_init returning shared_value.\n"
                      "Test with one operation and a wrong-device control.\n")
    project.finalize_stage(MigrationStage.CONTRACTS, (
        FileArtifact(MigrationArtifact.CONTRACTS, report),
        FileArtifact(MigrationArtifact.TEST_PORT_MATRIX, report),
    ))
    references = Path(project.config.skill_root) / "knowledge-guided-driver-port/references"
    for name in ("workflow.md", "test-porting.md", "target-changes.md", "qemu-evidence.md"):
        (references / name).write_text("# Fixture rule\n" + "Inspect original evidence.\n" * 80)
    return project


def runner(project):
    return PortRunner(
        PortOptions(
            workspace=project.root,
            source_platform="example-source",
            target_platform="example-target",
            driver_name="example-driver",
            skill_root=Path(project.config.skill_root),
            catalogs=(),
            backend=CodexBackend.EXEC,
            codex_bin="codex",
            model=None,
            analyzer="clang",
            analyzer_family=AnalyzerFamily.CLANG_LLVM,
        )
    )


class AlignmentTests(unittest.TestCase):
    def test_bounded_fact_query_uses_frozen_symbols_and_rejects_corruption(self):
        with tempfile.TemporaryDirectory() as directory:
            project = ready_implementation(Path(directory))
            result = query_facts(project, symbol="example_init", source_path="drivers/", limit=1)
            self.assertEqual(result["match_count"], 1)
            match = result["results"][0]
            self.assertEqual(match["identity"]["name"], "example_init")
            self.assertEqual(match["calls"][0]["target_name"], "shared_value")
            self.assertEqual(query_facts(project, symbol="missing")["results"], [])
            Path(match["semantic_index"]).write_text("tampered")
            with self.assertRaisesRegex(WorkflowError, "integrity"):
                query_facts(project, symbol="example_init")

    def test_out_of_tree_compile_captures_generated_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, build = root / "source", root / "build"
            source.mkdir()
            build.mkdir()
            (source / "driver.c").write_text(
                '#include "generated.h"\nint probe(void){return FLAG;}\n'
            )
            (build / "generated.h").write_text("#define FLAG 7\n")
            _, units, _, _ = SourceClosureService._derive_compile_inputs(
                source,
                [
                    {
                        "directory": str(build),
                        "file": str(source / "driver.c"),
                        "arguments": [
                            "/usr/bin/clang",
                            "-Wp,-MMD,unwanted.d",
                            "-I.",
                            str(source / "driver.c"),
                        ],
                    }
                ],
                workspace_root=root,
            )
            self.assertEqual(units[0]["generated_dependencies"][0]["path"], "build/generated.h")
            self.assertEqual(units[0]["compile_directory"], str(build))
            self.assertFalse((build / "unwanted.d").exists())

    def test_gateway_stdin_resume_compaction_and_sandbox(self):
        with tempfile.TemporaryDirectory() as directory:
            job = CodexJob(
                MigrationStage.DRIVER_IMPLEMENTATION,
                ActorRole.DEVELOPER,
                "implement",
                "x" * 200000,
                Path(directory),
                CodexSandbox.WORKSPACE_WRITE,
            )
            event = ('{"type":"item.completed","item":{"type":"agent_message","text":"ok"}}\n'
                     '{"type":"turn.completed"}\n')
            with patch(
                "driver_port_factory.codex.gateway.execute",
                return_value=CompletedProcess([], 0, event, ""),
            ) as run:
                CodexExecGateway().run(replace(job, thread_id="saved"))
            argv = run.call_args.args[0]
            self.assertEqual(argv[-1], "-")
            self.assertEqual(run.call_args.kwargs["prompt"], job.prompt)
            self.assertIn("model_auto_compact_token_limit=224000", argv)
            self.assertIn('sandbox_mode="workspace-write"', argv)
            self.assertNotIn(job.prompt, argv)
            self.assertIn("sandbox_workspace_write.network_access=true", argv)
            cargo_home = Path(directory) / ".dpf-output" / "cargo-home"
            self.assertTrue(cargo_home.is_dir())
            self.assertIn(
                f"shell_environment_policy.set.CARGO_HOME={json.dumps(str(cargo_home))}", argv
            )
            with patch(
                "driver_port_factory.codex.gateway.execute",
                return_value=CompletedProcess([], 0, event, ""),
            ) as run:
                CodexExecGateway().run(replace(job, stage=MigrationStage.PUBLIC_REPAIR))
            self.assertNotIn("sandbox_workspace_write.network_access=true", run.call_args.args[0])

    def test_worker_rework_preserves_implementation_session(self):
        with tempfile.TemporaryDirectory() as directory:
            project = ready_implementation(Path(directory))
            policy = CodexExecutionPolicy()

            def key(stage):
                return session_key(project, stage, policy.grant(project, stage), None, "exec")

            for stage in (EnvironmentStage.RECOVERY, TargetStudyStage.STUDY,
                          SourceAnalysisStage.SOURCE_CLOSURE, AcquisitionStage.REVISION_SELECTION,
                          AcquisitionStage.EVIDENCE_CLOSURE, MigrationStage.CONTRACTS):
                self.assertEqual(key(stage), key(MigrationStage.DRIVER_IMPLEMENTATION))
            self.assertEqual(
                key(MigrationStage.DRIVER_IMPLEMENTATION), key(MigrationStage.ARTIFACT_PREPARATION)
            )
            self.assertEqual(
                key(MigrationStage.DRIVER_IMPLEMENTATION),
                key(MigrationStage.PUBLIC_QEMU_VALIDATION),
            )
            self.assertNotEqual(
                key(MigrationStage.DRIVER_IMPLEMENTATION), key(MigrationStage.PUBLIC_REPAIR)
            )
            port = runner(project)
            worktree = project.root / load_repository_acquisition(project).target_worktree.path
            (worktree / "driver.rs").write_text("pub fn init() -> u32 { 1 }\n")
            (cache_root(project) / "manifest.json").unlink()
            calls = []

            def gateway(job):
                self.assertTrue(query_facts(project, symbol="example_init")["results"])
                calls.append(job)
                report = job.execution_root / ".dpf-output/report.md"
                report.parent.mkdir(exist_ok=True)
                ctx = json.loads(job.prompt.split("<job>", 1)[1].split("</job>", 1)[0])["context"]
                if ctx.get("repair_execution"):
                    (report.parent / "runtime-artifact").write_bytes((worktree / "driver.rs").read_bytes())
                    (report.parent / "check-presence.sh").write_text(
                        'cmp "$DPF_RUNTIME_ARTIFACT" "$DPF_TARGET_WORKTREE/driver.rs"\n')
                    (report.parent / "public-qemu.sh").write_text("exit 1\n")
                report.write_text("# Evidence\nImplementation coverage and limits.\nDPF_SELF_REVIEW: PASS\n")
                return CodexResult(
                    job.job_id,
                    f"REPORT_PATH: {report}\n",
                    job.thread_id or "implementation-session",
                )

            with patch("driver_port_factory.codex.cli.CodexExecGateway.run", side_effect=gateway):
                port._implementation(project)
                self.assertEqual(
                    project.stage(MigrationStage.DRIVER_IMPLEMENTATION).status, StageStatus.PASS
                )
                project.start(MigrationStage.ARTIFACT_PREPARATION)
                project.retry_from(MigrationStage.DRIVER_IMPLEMENTATION,
                    trigger=MigrationStage.ARTIFACT_PREPARATION, reason="source prerequisite")
                self.assertEqual(
                    project.stage(MigrationStage.DRIVER_IMPLEMENTATION).status, StageStatus.READY
                )
                port._implementation(project)
            self.assertIsNone(calls[0].thread_id)
            self.assertEqual(calls[1].thread_id, "implementation-session")
            resumed_job = json.loads(calls[1].prompt.split("<job>", 1)[1].split("</job>", 1)[0])
            changes = resumed_job["context"]["input_changes"]
            self.assertTrue(any(key.startswith("frozen_inputs/") for key in changes["unchanged"]))
            self.assertFalse(changes["changed"])
            self.assertIn("skill_document_unchanged", calls[1].prompt)
            self.assertIn("source_path=", calls[1].prompt)
            self.assertIn(str(Path(project.config.skill_root).resolve()), calls[1].prompt)
            self.assertLess(len(calls[1].prompt), len(calls[0].prompt))
            metrics = [json.loads(path.read_text()) for path in
                       (project.control / "codex").glob("driver_implementation-*.metrics.json")]
            self.assertEqual(len(metrics), 2)
            self.assertEqual(sorted(item["resumed"] for item in metrics), [False, True])
            self.assertTrue(all(item["elapsed_seconds"] >= 0 for item in metrics))

    def test_persistent_worker_uses_only_current_session(self):
        with tempfile.TemporaryDirectory() as directory:
            project = ready_implementation(Path(directory))
            stage = MigrationStage.DRIVER_IMPLEMENTATION
            grant = CodexExecutionPolicy().grant(project, stage)
            old = "obsolete-stage-session"
            save_session(project, old, "legacy-worker", {"skill": "hash"})
            key, session = stage_session(project, stage, grant, None, "exec")
            self.assertNotEqual(old, key)
            self.assertEqual(session, {})
            save_session(project, key, "persistent-worker", {"skill": "hash"})
            self.assertEqual(stage_session(project, stage, grant, None, "exec")[1]["thread_id"],
                             "persistent-worker")
            self.assertNotEqual(key, session_key(project, stage, grant, "different-model", "exec"))
            self.assertNotEqual(key, session_key(project, stage, grant, None, "sdk"))

    def test_artifact_checker_rejects_stale_payload_and_preserves_attempts(self):
        with tempfile.TemporaryDirectory() as directory:
            project = ready_implementation(Path(directory))
            worktree = project.root / load_repository_acquisition(project).target_worktree.path
            (worktree / "driver.rs").write_text("current driver payload\n")
            report = project.root / "report.md"
            report.write_text("# Build evidence\nCurrent payload must occur in the artifact.\nDPF_SELF_REVIEW: PASS\n")
            project.start(MigrationStage.DRIVER_IMPLEMENTATION)
            DriverImplementationService().snapshot_worktree(project, report)
            project.start(MigrationStage.ARTIFACT_PREPARATION)
            output = worktree / ".dpf-output"
            output.mkdir()
            (output / "runtime-artifact").write_text("stale payload\n")
            (output / "check-presence.sh").write_text(
                '#!/usr/bin/env bash\nset -euo pipefail\nitems=(driver)\n[[ ${items[0]} == driver ]]\n'
                'cmp "$DPF_RUNTIME_ARTIFACT" "$DPF_TARGET_WORKTREE/driver.rs"\n'
            )
            service = ArtifactPreparationService()
            with self.assertRaises(CodexOutputError):
                service.capture_codex_artifact(project, report)
            (output / "runtime-artifact").write_bytes((worktree / "driver.rs").read_bytes())
            identity = service.capture_codex_artifact(project, report)
            attempts = list((project.control / "artifact-preparation").iterdir())
            self.assertEqual(len(attempts), 2)
            self.assertEqual(identity["runtime_status"], "NOT_RUN")
            self.assertEqual(
                project.stage(MigrationStage.ARTIFACT_PREPARATION).status, StageStatus.PASS
            )
            project.verify_integrity()

    def test_repeated_output_defect_stops_without_infinite_api_calls(self):
        with tempfile.TemporaryDirectory() as directory:
            project = ready_implementation(Path(directory))
            port = runner(project)

            def reject(*_):
                raise CodexOutputError("unchanged defect")

            with (
                patch(
                    "driver_port_factory.codex.cli.CodexExecGateway.run",
                    return_value=CodexResult("bad", "bad report", "saved"),
                ) as gateway,
                self.assertRaisesRegex(CodexOutputError, "unchanged defect"),
            ):
                port._codex_gate(project, MigrationStage.DRIVER_IMPLEMENTATION, {}, reject)
            self.assertEqual(gateway.call_count, 2)

    def test_runtime_failure_stays_with_worker_and_routes_packaging(self):
        from tests.migration_support import packaged
        with tempfile.TemporaryDirectory() as directory:
            project, worktree, report = packaged(Path(directory))
            port = runner(project)
            project.start(MigrationStage.PUBLIC_QEMU_VALIDATION)
            script = worktree / ".dpf-output/public-qemu.sh"
            script.write_text("exit 1\n")
            report.write_text("# Ready\nDPF_RUN: PUBLIC_QEMU\n")
            with self.assertRaisesRegex(CodexOutputError, "Public harness failed"):
                PublicQemuService().run_script(project, script_path=script, work_report_path=report)
            self.assertEqual(project.stage(MigrationStage.PUBLIC_QEMU_VALIDATION).status, StageStatus.RUNNING)
            self.assertEqual(project.stage(MigrationStage.PUBLIC_REPAIR).status, StageStatus.PENDING)
            report.write_text("Missing guest entrypoint.\nDPF_REPAIR_STAGE: artifact_preparation\nDPF_REVIEW: REWORK\n")
            with patch("driver_port_factory.codex.cli.CodexExecGateway.run", return_value=CodexResult(
                "runtime", f"REPORT_PATH: {report}\n", "worker-session")) as gateway:
                port._public_qemu(project)
            self.assertEqual(gateway.call_count, 1)
            self.assertEqual(gateway.call_args.args[0].stage, MigrationStage.PUBLIC_QEMU_VALIDATION)
            self.assertEqual(project.stage(MigrationStage.DRIVER_IMPLEMENTATION).status, StageStatus.PASS)
            self.assertEqual(project.stage(MigrationStage.ARTIFACT_PREPARATION).status, StageStatus.READY)
            context = runner(project)._migration_context(project, ())
            self.assertIn("Missing guest entrypoint", Path(context["runtime_work_report_path"]).read_text())


if __name__ == "__main__":
    unittest.main()
