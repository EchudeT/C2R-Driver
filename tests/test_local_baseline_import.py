from __future__ import annotations

import subprocess

import pytest

from driver_port_factory.acquisition.commands import RepositoryCommandKind
from driver_port_factory.acquisition.git_execution import RepositoryGit
from driver_port_factory.acquisition.repository_role import RepositoryRole
from driver_port_factory.acquisition.repository_spec import RepositorySpec
from driver_port_factory.acquisition.repository_storage import BareRepositoryStore
from driver_port_factory.core.models import WorkflowError


def _git(*arguments: str, cwd):
    return subprocess.run(
        ["git", *arguments], cwd=cwd, check=True, text=True, capture_output=True
    ).stdout.strip()


def test_local_baseline_import_uses_commit_objects_only(tmp_path):
    local = tmp_path / "linux"
    local.mkdir()
    _git("init", "-b", "main", cwd=local)
    _git("config", "user.name", "DPF test", cwd=local)
    _git("config", "user.email", "dpf@example.invalid", cwd=local)
    (local / "drivers").mkdir()
    (local / "drivers/e1000.c").write_text("committed source\n", encoding="utf-8")
    _git("add", ".", cwd=local)
    _git("commit", "-m", "fixture", cwd=local)
    commit = _git("rev-parse", "HEAD", cwd=local)
    # A dirty edit must stay in the user checkout and never enter the run.
    (local / "drivers/e1000.c").write_text("uncommitted edit\n", encoding="utf-8")

    project_root = tmp_path / "run"
    control_root = project_root / ".dpf"
    control_root.mkdir(parents=True)
    git = RepositoryGit(project_root, control_root)
    spec = RepositorySpec(
        RepositoryRole.SOURCE,
        "linux",
        "https://invalid.example/linux.git",
        commit,
        commit,
        "fixture commit",
    )
    store = BareRepositoryStore(
        project_root,
        control_root,
        git,
        local_repositories={RepositoryRole.SOURCE: local},
    )

    bare = store.prepare(spec)
    store.fetch(bare, spec)
    fetched = git.run(
        ["-C", str(bare), "rev-parse", "FETCH_HEAD^{commit}"],
        operation=RepositoryCommandKind.BASELINE_COMMIT,
        role=RepositoryRole.SOURCE,
    ).stdout

    assert fetched == commit
    fetches = [
        record.result.argv
        for record in git.records
        if record.operation is RepositoryCommandKind.BASELINE_FETCH
    ]
    assert len(fetches) == 1
    assert str(local) in fetches[0]
    assert "origin" not in fetches[0]
    first_fetch_evidence = next(
        record.result.stdout_path
        for record in git.records
        if record.operation is RepositoryCommandKind.BASELINE_FETCH
    )

    # A controller restart reuses the local import receipt rather than fetching
    # the same commit a second time.
    restarted_git = RepositoryGit(project_root, control_root)
    restarted_store = BareRepositoryStore(
        project_root,
        control_root,
        restarted_git,
        local_repositories={RepositoryRole.SOURCE: local},
    )
    restarted_store.fetch(bare, spec)
    restarted_fetches = [
        record.result.stdout_path
        for record in restarted_git.records
        if record.operation is RepositoryCommandKind.BASELINE_FETCH
    ]
    assert restarted_fetches == [
        first_fetch_evidence
    ], "restart should reuse the recorded local fetch evidence"

    checkout = control_root / "worktrees" / "source"
    git.run(
        ["-C", str(bare), "worktree", "add", "--detach", str(checkout), commit],
        operation=RepositoryCommandKind.BASELINE_WORKTREE,
        role=RepositoryRole.SOURCE,
    )
    assert (checkout / "drivers/e1000.c").read_text(encoding="utf-8") == "committed source\n"
    assert (local / "drivers/e1000.c").read_text(encoding="utf-8") == "uncommitted edit\n"


def test_local_baseline_import_fails_without_requested_commit(tmp_path):
    local = tmp_path / "linux"
    local.mkdir()
    _git("init", "-b", "main", cwd=local)
    _git("config", "user.name", "DPF test", cwd=local)
    _git("config", "user.email", "dpf@example.invalid", cwd=local)
    (local / "README").write_text("fixture\n", encoding="utf-8")
    _git("add", ".", cwd=local)
    _git("commit", "-m", "fixture", cwd=local)

    project_root = tmp_path / "run"
    control_root = project_root / ".dpf"
    control_root.mkdir(parents=True)
    git = RepositoryGit(project_root, control_root)
    spec = RepositorySpec(
        RepositoryRole.SOURCE,
        "linux",
        "https://invalid.example/linux.git",
        "does-not-exist",
        "0" * 40,
        "fixture missing ref",
    )
    store = BareRepositoryStore(
        project_root,
        control_root,
        git,
        local_repositories={RepositoryRole.SOURCE: local},
    )
    bare = store.prepare(spec)

    with pytest.raises(WorkflowError, match="does not contain requested revision"):
        store.fetch(bare, spec)
