from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from driver_port_factory.cli import parser
from driver_port_factory.codex.contracts import CodexBackend
from driver_port_factory.core.models import StageStatus
from driver_port_factory.knowledge.contracts import KnowledgeDependencyClosureError, KnowledgeStage
from driver_port_factory.port import PortOptions, PortRunner
from driver_port_factory.source_analysis.clang_backend import AnalyzerFamily


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
    def test_dependency_closure_failure_is_not_sent_back_to_codex(self) -> None:
        runner = PortRunner(options(Path("/unused")))
        result = SimpleNamespace(thread_id="knowledge-thread")

        def reject(_project, _job) -> None:
            raise KnowledgeDependencyClosureError("deterministic Cargo failure")

        with (
            patch.object(runner, "_latest_job_occurrence", return_value=None),
            patch.object(runner, "_codex", return_value=(result, None, Path("response"))) as codex,
            patch.object(runner, "_job_occurrence", return_value=object()),
            self.assertRaisesRegex(KnowledgeDependencyClosureError, "deterministic Cargo failure"),
        ):
            runner._codex_gate(
                object(),
                KnowledgeStage.KNOWLEDGE_BASE,
                "editable objective",
                {},
                reject,
            )

        self.assertEqual(codex.call_count, 1)

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
