from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from driver_port_factory.acquisition.job import ArtifactOccurrence
from driver_port_factory.cli import parser
from driver_port_factory.codex.contracts import CodexBackend, CodexOutputError
from driver_port_factory.core.models import StageStatus, WorkflowError
from driver_port_factory.knowledge.bootstrap import KnowledgeBootstrapper
from driver_port_factory.knowledge.contracts import KnowledgeStage
from driver_port_factory.migration.contracts import MigrationStage
from driver_port_factory.port import PortOptions, PortRunner
from driver_port_factory.source_analysis.clang_backend import AnalyzerFamily
from tests.test_knowledge import prepare_project, probe_plan
from tests.test_target_study import target_study_inputs


class FakeProject:
    def __init__(self, stages) -> None:
        self._stages = {
            name: SimpleNamespace(name=name, position=position, status=StageStatus.READY)
            for position, name in enumerate(stages)
        }
        self.artifacts = SimpleNamespace(path_for_digest=lambda _digest: Path("audit.json"))

    def stages(self):
        return list(self._stages.values())

    def stage(self, name):
        return self._stages[name]

    @staticmethod
    def artifact(_stage, _kind):
        return SimpleNamespace(digest="a" * 64)


def options(root: Path) -> PortOptions:
    return PortOptions(
        workspace=root,
        source_platform="linux",
        target_platform="starryos",
        driver_name="ne2k-pci",
        skill_root=root,
        catalogs=(),
        backend=CodexBackend.EXEC,
        codex_bin="fake-codex",
        model=None,
        analyzer="fake-clang",
        analyzer_family=AnalyzerFamily.CLANG_LLVM,
    )


class PortRunnerTests(unittest.TestCase):
    def test_implementation_gate_failure_is_a_same_thread_correction(self) -> None:
        project = SimpleNamespace(
            artifacts=SimpleNamespace(path_for_digest=lambda _digest: Path("response.json"))
        )
        job = ArtifactOccurrence("a" * 64, 1)

        with (
            patch("driver_port_factory.port.ImplementationResponse.read", return_value=object()),
            patch(
                "driver_port_factory.port.DriverImplementationService.finalize",
                side_effect=WorkflowError("target change inventory is incomplete"),
            ),
            self.assertRaisesRegex(
                CodexOutputError,
                "driver implementation failed: target change inventory is incomplete",
            ),
        ):
            PortRunner._accept_implementation_result(project, job)

    def test_compliance_implementation_finding_retries_smallest_gate(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            report = root / "compliance.json"
            plan = root / "unused-plan.json"
            report.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "status": "VIOLATION",
                        "areas": [
                            {
                                "area": "API_USAGE",
                                "status": "VIOLATION",
                                "repair_target": "IMPLEMENTATION",
                                "summary": "Target wiring is missing.",
                                "implementation_paths": ["driver.rs"],
                                "target_evidence": [],
                                "unsafe_obligation_ids": [],
                                "details": {},
                            },
                            {
                                "area": "DOCUMENTATION",
                                "status": "UNKNOWN",
                                "repair_target": "KNOWLEDGE",
                                "summary": "Documentation evidence is incomplete.",
                                "implementation_paths": ["driver.rs"],
                                "target_evidence": [],
                                "unsafe_obligation_ids": [],
                                "details": {},
                            },
                        ],
                        "apis": [],
                        "target_changes": [],
                        "execution": {"compile": "NOT_RUN", "runtime": "NOT_RUN"},
                    }
                ),
                encoding="utf-8",
            )
            plan.write_text("not parsed for implementation repair", encoding="utf-8")
            project = SimpleNamespace(retry_from=Mock())
            runner = PortRunner(options(root))
            job = ArtifactOccurrence("a" * 64, 3)

            with patch.object(
                runner,
                "_write_response_parts",
                return_value={"compliance_report": report, "artifact_preparation_plan": plan},
            ):
                runner._accept_compliance_result(project, job)

            project.retry_from.assert_called_once_with(
                MigrationStage.DRIVER_IMPLEMENTATION,
                trigger=MigrationStage.TARGET_COMPLIANCE,
                reason=(
                    "target compliance requested implementation repair from "
                    f"job {job.digest}:{job.ordinal}"
                ),
            )

    def test_operational_gate_failure_is_not_sent_back_to_codex(self) -> None:
        runner = PortRunner(options(Path("/unused")))
        result = SimpleNamespace(thread_id="knowledge-thread")

        def reject(_project, _job) -> None:
            raise WorkflowError("deterministic index failure")

        with (
            patch.object(runner, "_latest_job_occurrence", return_value=None),
            patch.object(runner, "_codex", return_value=(result, None, Path("response"))) as codex,
            patch.object(runner, "_job_occurrence", return_value=object()),
            self.assertRaisesRegex(WorkflowError, "deterministic index failure"),
        ):
            runner._codex_gate(
                object(),
                KnowledgeStage.KNOWLEDGE_BASE,
                {},
                reject,
            )

        self.assertEqual(codex.call_count, 1)

    def test_output_gate_failure_reuses_thread_for_correction(self) -> None:
        runner = PortRunner(options(Path("/unused")))
        result = SimpleNamespace(thread_id="same-thread")
        attempts = 0

        def accept(_project, _job) -> None:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise CodexOutputError("invalid proposal")

        with (
            patch.object(runner, "_latest_job_occurrence", return_value=None),
            patch.object(runner, "_codex", return_value=(result, None, Path("response"))) as codex,
            patch.object(runner, "_job_occurrence", return_value=object()),
        ):
            runner._codex_gate(object(), KnowledgeStage.KNOWLEDGE_BASE, {}, accept)

        self.assertEqual(codex.call_count, 2)
        self.assertEqual(codex.call_args.kwargs["thread_id"], "same-thread")
        self.assertEqual(codex.call_args.kwargs["follow_up"], "invalid proposal")

    def test_target_study_document_gate_is_codex_output_error(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project, checkouts = prepare_project(root)
            KnowledgeBootstrapper().bootstrap(project, probe_plan_path=probe_plan(project.root))
            parts, _ = target_study_inputs(project.root, project, checkouts)
            parts["api_table"].write_text("[]", encoding="utf-8")
            runner = PortRunner(options(root))

            with patch.object(runner, "_write_response_parts", return_value=parts):
                for _ in range(2):
                    with self.assertRaisesRegex(CodexOutputError, "JSON must be an object"):
                        runner._accept_target_study_result(project, object())

    def test_fresh_port_workspace_is_its_own_git_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary) / "fresh-port"
            project = PortRunner(options(workspace))._project()
            git_root = subprocess.run(
                ["git", "rev-parse", "--show-toplevel"],
                cwd=workspace,
                text=True,
                capture_output=True,
                check=True,
            ).stdout.strip()

            self.assertEqual(Path(git_root), workspace.resolve())
            self.assertEqual(project.root, workspace.resolve())

    def test_single_entry_runs_typed_dag_to_completion(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            runner = PortRunner(options(Path(temporary)))
            project = FakeProject(tuple(runner._actions))
            calls = []
            runner._actions = {
                name: lambda active, stage=name: (
                    calls.append(stage),
                    setattr(active.stage(stage), "status", StageStatus.PASS),
                )
                for name in runner._actions
            }
            with patch.object(runner, "_project", return_value=project):
                outcome = runner.run()

            self.assertEqual(calls, list(runner._actions))
            self.assertEqual(outcome.status, StageStatus.PASS)
            self.assertEqual(outcome.next_action, "complete")
            self.assertEqual(outcome.audit_path, "audit.json")

    def test_second_run_resumes_without_repeating_completed_stages(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            runner = PortRunner(options(Path(temporary)))
            project = FakeProject(tuple(runner._actions))
            calls = {name: 0 for name in runner._actions}
            stop = tuple(runner._actions)[4]

            def execute(active, stage):
                calls[stage] += 1
                active.stage(stage).status = (
                    StageStatus.RUNNING if stage is stop and calls[stage] == 1 else StageStatus.PASS
                )

            runner._actions = {
                name: lambda active, stage=name: execute(active, stage) for name in runner._actions
            }
            with patch.object(runner, "_project", return_value=project):
                first = runner.run()
                second = runner.run()

            self.assertEqual(first.status, StageStatus.RUNNING)
            self.assertEqual(second.status, StageStatus.PASS)
            self.assertTrue(all(calls[name] == 1 for name in tuple(runner._actions)[:4]))
            self.assertEqual(calls[stop], 2)

    def test_cli_requires_only_migration_identity_and_workspace(self) -> None:
        arguments = parser().parse_args(
            [
                "port",
                "run",
                "/tmp/work",
                "--source-platform",
                "linux",
                "--target-platform",
                "starryos",
                "--driver-name",
                "ne2k-pci",
            ]
        )
        self.assertEqual(arguments.backend, CodexBackend.EXEC)
        self.assertEqual(arguments.driver_name, "ne2k-pci")


if __name__ == "__main__":
    unittest.main()
