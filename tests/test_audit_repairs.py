"""Regression cases from the workflow audit; no model API or real driver execution."""
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from driver_port_factory.acquisition.document_binding import ExternalDocumentBinder
from driver_port_factory.acquisition.job import ArtifactOccurrence
from driver_port_factory.codex.contracts import CodexArtifact, CodexOutputError
from driver_port_factory.codex.transport import execute
from driver_port_factory.core.models import StageStatus, WorkflowError
from driver_port_factory.core.trace import qemu_experiment
from driver_port_factory.migration.contracts import MigrationStage as S
from driver_port_factory.migration.implementation import (
    ImplementationChanged, validate_worktree_snapshot, worktree_files,
)
from driver_port_factory.migration.public_qemu import runtime_in_qemu_arguments
from driver_port_factory.migration.repair_routing import WorkerBlocked
from driver_port_factory.port import PortRunner
from driver_port_factory.source_analysis.ast_scope import DeclarationScope
from driver_port_factory.source_analysis.closure import _normalize_compile_command


def git(root, *args):
    return subprocess.run(["git", "-C", str(root), "-c", "user.name=fixture",
                           "-c", "user.email=fixture@example.invalid", *args],
                          text=True, capture_output=True, check=True).stdout.strip()


def test_git_checkpoints_deletions_renames_modes_and_reports(tmp_path):
    work = tmp_path / "target"
    work.mkdir()
    git(work, "init")
    (work / "old.rs").write_text("old code\n")
    (work / "remove.rs").write_text("obsolete\n")
    git(work, "add", ".")
    git(work, "commit", "-m", "upstream")
    base = git(work, "rev-parse", "HEAD")
    (work / "old.rs").rename(work / "new.rs")
    (work / "remove.rs").unlink()
    git(work, "add", "-A")
    git(work, "commit", "-m", "local Skill checkpoint")
    files = worktree_files(work, base)
    assert {f["path"]: f["state"] for f in files} == {
        "old.rs": "deleted", "remove.rs": "deleted", "new.rs": "file"}
    bundle = {"target_worktree": {"path": "target", "base_commit": base}, "files": files}
    output = work / ".dpf-output"
    output.mkdir()
    (output / "packaging-report.md").write_text("report only\n")
    assert validate_worktree_snapshot(tmp_path, bundle) == work
    (work / "new.rs").chmod(0o755)
    with pytest.raises(ImplementationChanged):
        validate_worktree_snapshot(tmp_path, bundle)
    (work / "new.rs").chmod(0o644)
    (work / "link.rs").symlink_to(work / "new.rs")
    with pytest.raises(CodexOutputError, match="symlink"):
        worktree_files(work, base)


@pytest.mark.parametrize("reply", ["done", "REPORT_PATH: /tmp/plan.txt",
    "REPORT_PATH: /tmp/a.md\nREPORT_PATH: /tmp/b.md", ""])
def test_invalid_report_protocol_never_freezes_chat_text(tmp_path, reply):
    ref = SimpleNamespace(kind=CodexArtifact.JOB_RESULT.value, digest="job", ordinal=0,
                          source="worker")
    project = Mock(root=tmp_path)
    project.current_artifact_refs.return_value = [ref]
    project.artifacts.read.return_value = reply.encode()
    with pytest.raises(CodexOutputError):
        PortRunner._materialize_codex_report(project, S.CONTRACTS, ArtifactOccurrence("job", 0))
    project.record_artifact.assert_not_called()


def test_blocker_is_recorded_without_repeated_pass_corrections():
    runner = object.__new__(PortRunner)
    project = Mock()
    with patch.object(runner, "_latest_job_occurrence", return_value=object()), \
         patch.object(runner, "_latest_thread_id", return_value=None), \
         patch.object(runner, "_codex") as model:
        assert not runner._codex_gate(project, S.DRIVER_IMPLEMENTATION, {},
                                     Mock(side_effect=WorkerBlocked("missing tool")))
    project.complete.assert_called_once_with(S.DRIVER_IMPLEMENTATION, StageStatus.BLOCKED,
                                            message="missing tool")
    model.assert_not_called()


@pytest.mark.parametrize("args,bound", [
    (["-kernel", "/tmp/frozen"], True),
    (["-drive", "file=/tmp/frozen,format=raw"], True),
    (["-D", "/tmp/frozen", "-kernel", "/tmp/other"], False),
    (["-name", "/tmp/frozen"], False),
    (["--version", "-kernel", "/tmp/frozen"], False),
])
def test_only_boot_arguments_bind_the_frozen_image(args, bound):
    line = 'execve("qemu-system-x86_64", ' + json.dumps(["qemu", *args]) + ', []) = 0'
    assert runtime_in_qemu_arguments(line, Path("/tmp/frozen")) is bound
    if "--version" in args:
        assert not qemu_experiment(line)


def test_empty_compiler_command_is_actionable(tmp_path):
    (tmp_path / "entry.c").write_text("int entry(void) {return 0;}\n")
    with pytest.raises(WorkflowError, match="compile command"):
        _normalize_compile_command(tmp_path, {"file": "entry.c", "arguments": []})


@pytest.mark.skipif(not shutil.which("clang"), reason="Clang AST required")
def test_compiler_alias_retains_nonroot_definition():
    result = subprocess.run(["clang", "-x", "c", "-Xclang", "-ast-dump=json", "-fsyntax-only", "-"],
        input='int helper(void); int entry(void) __attribute__((alias("helper"))); '
              'int helper(void) {return 42;}', text=True, capture_output=True, check=True)
    nodes = [node for node in json.loads(result.stdout)["inner"] if node["kind"] == "FunctionDecl"]
    scope = DeclarationScope()
    for node in nodes:
        scope.add(node, root=node["name"] == "entry")
    assert scope.selected() == set(range(len(nodes)))


def test_external_document_hashes_are_derived_not_model_supplied():
    binder = object.__new__(ExternalDocumentBinder)
    binder.content = Mock()
    def response(url):
        return SimpleNamespace(response=SimpleNamespace(requested_url=url, resolved_url=url,
                                                       sha256="a" * 64))
    urls = ["https://publisher.invalid/manual.pdf", "https://archive.invalid/manual.pdf"]
    binder.content.retrieve.side_effect = [response(url) for url in urls]
    locator = binder.bind({"url": urls[0], "corroboration_urls": urls[1:]})
    assert locator.expected_sha256 == "a" * 64
    assert locator.authority.sources[0].expected_sha256 == "a" * 64
    assert binder.content.retrieve.call_args.kwargs["expected_sha256"] == "a" * 64


def test_silent_model_cli_is_bounded_and_partial_unicode_is_preserved(tmp_path):
    with pytest.raises(WorkflowError, match="deadline"):
        execute([sys.executable, "-c", "import time; time.sleep(10)"], cwd=tmp_path,
                prompt="", idle_timeout_seconds=0.1, timeout_seconds=1)
    events = []
    result = execute([sys.executable, "-c", 'print(\'{"text":"证据"}\')'], cwd=tmp_path,
                     prompt="", on_event=events.append)
    assert result.returncode == 0 and events == [{"text": "证据"}]


@pytest.mark.parametrize("idle,total", [(0.2, 5), (5, 0.2)])
def test_eof_does_not_disable_either_deadline(tmp_path, idle, total):
    started = time.monotonic()
    with pytest.raises(WorkflowError, match="deadline"):
        execute([sys.executable, "-c", "import os,time; os.close(1); time.sleep(10)"],
                cwd=tmp_path, prompt="", idle_timeout_seconds=idle, timeout_seconds=total)
    assert time.monotonic() - started < 3
