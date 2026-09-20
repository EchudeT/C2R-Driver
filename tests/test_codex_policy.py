from __future__ import annotations

import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path
from subprocess import CompletedProcess
from typing import Any
from unittest.mock import patch

from driver_port_factory.acquisition.contracts import AcquisitionStage
from driver_port_factory.cli import parser
from driver_port_factory.codex.contracts import CodexArtifact, CodexSandbox
from driver_port_factory.codex.gateway import CodexExecGateway, CodexJob
from driver_port_factory.codex.policy import CodexExecutionPolicy
from driver_port_factory.composition import initialize_project
from driver_port_factory.core.models import (
    ActorRole,
    EvaluationMode,
    GeneratedArtifact,
    ProjectConfig,
    StageOwner,
    StageStatus,
    WorkflowError,
)
from driver_port_factory.evaluation.contracts import EvaluationStage
from driver_port_factory.migration.contracts import MigrationStage


@dataclass(slots=True)
class PolicyProject:
    root: Path
    manifest: dict[str, Any]

    @property
    def control(self) -> Path:
        return self.root / ".dpf"

    def load_json_artifact(self, *_: object) -> dict[str, Any]:
        return self.manifest


def checkout(role: str, path: str) -> dict[str, Any]:
    return {
        "role": role,
        "platform": role,
        "source_url": f"https://example.invalid/{role}.git",
        "requested_ref": "main",
        "resolved_commit": "1" * 40,
        "tree_id": "2" * 40,
        "bare_repository": f".dpf/repositories/{role}.git",
        "checkout_path": path,
        "clean": True,
        "lock_path": f".dpf/manifests/repository-locks/{role}.json",
        "lock_sha256": "3" * 64,
        "acquired_at": "2026-09-16T00:00:00Z",
    }


def repository_manifest(target_worktree: str, checkouts: list[dict[str, Any]]) -> dict[str, Any]:
    target = next(checkout for checkout in checkouts if checkout["role"] == "target")
    return {
        "schema_version": 1,
        "project_id": "policy-test",
        "acquired_at": "2026-09-16T00:00:00Z",
        "migration_envelope_sha256": "4" * 64,
        "repository_plan_sha256": "5" * 64,
        "target_worktree": {
            "path": target_worktree,
            "base_commit": target["resolved_commit"],
            "branch": "dpf/policy-test",
            "observation": {
                "head_commit": target["resolved_commit"],
                "branch": "dpf/policy-test",
                "git_dir": ".dpf/repositories/target.git/worktrees/target-working",
                "work_tree": target_worktree,
                "status_sha256": "6" * 64,
                "dirty": False,
            },
        },
        "checkouts": checkouts,
        "commands": [repository_command()],
    }


def repository_command() -> dict[str, Any]:
    return {
        "operation": "target_status_observation",
        "role": "target",
        "result": {
            "argv": ["git", "status"],
            "cwd": "/tmp/policy-test",
            "started_at": "2026-09-16T00:00:00Z",
            "completed_at": "2026-09-16T00:00:01Z",
            "exit_code": 0,
            "launched": True,
            "launch_error": None,
            "timed_out": False,
            "duration_milliseconds": 1,
            "stdout_sha256": "7" * 64,
            "stderr_sha256": "8" * 64,
            "stdout_path": "/tmp/policy-test.stdout",
            "stderr_path": "/tmp/policy-test.stderr",
        },
    }


def project_config() -> ProjectConfig:
    return ProjectConfig(
        project_id="codex-policy-test",
        source_platform="linux",
        target_platform="asterinas",
        driver_name="generic-driver",
        evaluation_mode=EvaluationMode.DEVELOPER_EVIDENCE,
        actor_role=ActorRole.DEVELOPER,
    )


class CodexPolicyTests(unittest.TestCase):
    def test_only_implementation_and_repair_receive_workspace_write(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            paths = {
                "source": ".dpf/checkouts/source",
                "target": ".dpf/checkouts/target",
                "qemu": ".dpf/checkouts/qemu",
            }
            for relative in (*paths.values(), "work/target-working", ".dpf"):
                (root / relative).mkdir(parents=True, exist_ok=True)
            manifest = repository_manifest(
                "work/target-working",
                [checkout(role, path) for role, path in paths.items()],
            )
            policy_project = PolicyProject(root, manifest)
            policy = CodexExecutionPolicy()

            writable = policy.grant(policy_project, MigrationStage.DRIVER_IMPLEMENTATION)
            repair = policy.grant(policy_project, MigrationStage.PUBLIC_REPAIR)
            readonly = policy.grant(policy_project, MigrationStage.CONTRACTS)
            networked = policy.grant(policy_project, AcquisitionStage.REVISION_SELECTION)

            self.assertEqual(writable.sandbox, CodexSandbox.WORKSPACE_WRITE)
            self.assertEqual(repair.sandbox, CodexSandbox.WORKSPACE_WRITE)
            self.assertEqual(repair.execution_root, root / "work/stage-work/public_repair")
            self.assertEqual(writable.execution_root, root / "work/target-working")
            self.assertEqual(readonly.sandbox, CodexSandbox.WORKSPACE_WRITE)
            self.assertEqual(readonly.execution_root, root / "work/stage-work/migration_contracts")
            self.assertNotEqual(readonly.execution_root, writable.execution_root)
            self.assertEqual(networked.sandbox, CodexSandbox.UNRESTRICTED)
            self.assertEqual(networked.execution_root, root)
            self.assertNotEqual(writable.execution_root, root)
            self.assertNotIn(root / ".dpf", writable.execution_root.parents)

    def test_independent_evaluation_and_audit_stages_are_read_only(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            policy_project = PolicyProject(root, {})
            for stage in (
                EvaluationStage.CONTRACT_FREEZE,
                EvaluationStage.ISOLATION_GATE,
                EvaluationStage.INDEPENDENCE_AUDIT,
                EvaluationStage.CLAIM_AUDIT,
            ):
                with self.subTest(stage=stage.value):
                    grant = CodexExecutionPolicy().grant(policy_project, stage)
                    self.assertEqual(grant.sandbox, CodexSandbox.READ_ONLY)
                    self.assertEqual(grant.execution_root, root)

    def test_exec_gateway_starts_a_persistent_job(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            job = CodexJob(
                stage=EvaluationStage.ISOLATION_GATE,
                actor_role=ActorRole.EVALUATOR,
                objective="verify isolation",
                prompt="bounded evaluator prompt",
                execution_root=root,
                sandbox=CodexSandbox.READ_ONLY,
            )
            output = (
                '{"type":"thread.started","thread_id":"fresh-thread"}\n'
                '{"type":"item.completed","item":'
                '{"type":"agent_message","text":"ok"}}'
            )
            with patch(
                "driver_port_factory.codex.gateway.execute",
                return_value=CompletedProcess([], 0, stdout=output, stderr=""),
            ) as run:
                result = CodexExecGateway("codex").run(job)
            command = run.call_args.args[0]
            self.assertNotIn("--ephemeral", command)
            self.assertNotIn("resume", command)
            self.assertEqual(result.thread_id, "fresh-thread")

    def test_exec_gateway_resumes_an_existing_thread(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            job = CodexJob(
                stage=EvaluationStage.ISOLATION_GATE,
                actor_role=ActorRole.EVALUATOR,
                objective="correct result",
                prompt="controller feedback",
                execution_root=Path(temporary),
                sandbox=CodexSandbox.READ_ONLY,
                thread_id="existing-thread",
            )
            output = (
                '{"type":"item.completed","item":'
                '{"type":"agent_message","text":"ok"}}'
            )
            with patch(
                "driver_port_factory.codex.gateway.execute",
                return_value=CompletedProcess([], 0, stdout=output, stderr=""),
            ) as run:
                CodexExecGateway("codex").run(job)
            command = run.call_args.args[0]
            self.assertEqual(command[:6], ["codex", "--cd", str(Path(temporary).resolve()),
                                          "exec", "resume", "--json"])
            self.assertIn("existing-thread", command)
            self.assertNotIn("--sandbox", command)

    def test_workspace_write_rejects_control_and_every_frozen_checkout_overlap(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            frozen = {
                "source": ".dpf/checkouts/source",
                "target": ".dpf/checkouts/target",
                "qemu": ".dpf/checkouts/qemu",
            }
            for relative in (*frozen.values(), ".dpf"):
                (root / relative).mkdir(parents=True, exist_ok=True)
            base = [checkout(role, path) for role, path in frozen.items()]
            forbidden = (".", ".dpf", *frozen.values())
            for target in forbidden:
                with self.subTest(target=target):
                    policy_project = PolicyProject(
                        root,
                        repository_manifest(target, base),
                    )
                    with self.assertRaises(WorkflowError):
                        CodexExecutionPolicy().grant(
                            policy_project, MigrationStage.DRIVER_IMPLEMENTATION
                        )

    def test_cli_exposes_no_filesystem_privilege_or_thread_override(self) -> None:
        options = {option for action in parser()._actions for option in self._all_options(action)}
        self.assertTrue(
            {
                "--sandbox",
                "--thread-id",
                "--finalize-as",
                "--danger-full-access",
                "--dangerously-bypass-approvals-and-sandbox",
                "--schema",
            }.isdisjoint(options)
        )

    @classmethod
    def _all_options(cls, action: Any) -> tuple[str, ...]:
        options = list(action.option_strings)
        choices = getattr(action, "choices", None)
        if isinstance(choices, dict):
            for subparser in choices.values():
                for child in subparser._actions:
                    options.extend(cls._all_options(child))
        return tuple(options)

    def test_codex_evidence_is_auxiliary_and_cannot_mark_a_stage_pass(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cases = (
                (
                    "migration",
                    project_config(),
                    MigrationStage.DRIVER_IMPLEMENTATION,
                    b"{}",
                ),
                (
                    "evaluation",
                    ProjectConfig(
                        project_id="codex-evaluation-policy-test",
                        source_platform="linux",
                        target_platform="asterinas",
                        driver_name="generic-driver",
                        evaluation_mode=EvaluationMode.PROSPECTIVE_BLIND,
                        actor_role=ActorRole.EVALUATOR,
                    ),
                    EvaluationStage.ISOLATION_GATE,
                    b'{"status":"PASS"}',
                ),
            )
            for label, configuration, stage_key, response in cases:
                with self.subTest(domain=label):
                    project = initialize_project(root / label, configuration)
                    for stage in project.stages():
                        required = {item.value for item in stage.required_outputs}
                        self.assertTrue(required.isdisjoint(CodexArtifact))
                        if stage.owner in {
                            StageOwner.CODEX,
                            StageOwner.HYBRID,
                            StageOwner.INDEPENDENT,
                        }:
                            self.assertIn(CodexArtifact.JOB_RESULT, stage.auxiliary_outputs)
                            self.assertIn(CodexArtifact.EVENT_LOG, stage.auxiliary_outputs)

                    with project._persistence._connect() as connection:
                        connection.execute(
                            "UPDATE stages SET status = ? WHERE name = ?",
                            (StageStatus.READY.value, stage_key.value),
                        )
                    project.start(stage_key)
                    result = GeneratedArtifact(
                        CodexArtifact.JOB_RESULT,
                        response,
                        f"test:{label}-codex-result",
                    )
                    project.record_artifact(stage_key, result)
                    self.assertEqual(project.stage(stage_key).status, StageStatus.RUNNING)
                    with self.assertRaisesRegex(WorkflowError, "invalid final output bundle"):
                        project.finalize_stage(stage_key, (result,))
                    self.assertEqual(project.stage(stage_key).status, StageStatus.RUNNING)


if __name__ == "__main__":
    unittest.main()
