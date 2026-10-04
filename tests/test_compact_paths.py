"""Short physical navigation never substitutes for full revision/content identities."""

import json
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import pytest

from driver_port_factory.acquisition.repository import RepositoryAcquirer
from driver_port_factory.acquisition.short_paths import allocate
from driver_port_factory.composition import open_project
from driver_port_factory.core.artifacts import ArtifactStore
from driver_port_factory.core.models import ProjectConfig, WorkflowError
from driver_port_factory.short_refs import References, compact
from tests.repository_support import project_config, ready_project


@pytest.mark.parametrize("short", [False, True])
def test_real_acquisition_reopens_with_full_identity_and_selected_paths(tmp_path, short):
    config = replace(project_config(), compact_paths=short)
    with patch("tests.repository_support.project_config", return_value=config):
        project, *_ = ready_project(tmp_path)
    acquisition = RepositoryAcquirer().acquire(project)
    if short:
        assert acquisition.target_worktree.path == "work/target"
        assert {c.checkout_path for c in acquisition.checkouts} == {
            ".dpf/worktrees/source",
            ".dpf/worktrees/target",
            ".dpf/worktrees/qemu",
        }
    else:
        assert acquisition.target_worktree.base_commit in acquisition.target_worktree.path
    assert all(len(c.resolved_commit) == 40 for c in acquisition.checkouts)
    assert len(acquisition.target_worktree.base_commit) == 40
    assert RepositoryAcquirer().acquire(open_project(project.root)) == acquisition
    project.verify_integrity()


def test_new_revision_allocates_new_directory_without_retargeting_or_deleting(tmp_path):
    control = tmp_path / ".dpf"
    control.mkdir()
    first = allocate(tmp_path, control, "target", {"commit": "first"}, writable=True)
    first.mkdir(parents=True)
    (first / "driver.rs").write_text("retained implementation")
    assert allocate(tmp_path, control, "target", {"commit": "first"}, writable=True) == first
    second = allocate(tmp_path, control, "target", {"commit": "second"}, writable=True)
    assert second == tmp_path / "work/target-2"
    assert (first / "driver.rs").read_text() == "retained implementation"


def test_evidence_alias_is_real_stable_and_cannot_point_to_replacement(tmp_path):
    control = tmp_path / ".dpf"
    control.mkdir()
    (control / "project.json").write_text('{"compact_paths":true}')
    store = ArtifactStore(control / "cas")
    original = store.put_bytes(b"original evidence", kind="fixture")
    path = store.path_for_digest(original.digest)
    refs = References(tmp_path)
    shown = compact({"path": str(path), "digest": original.digest}, refs)
    alias = Path(shown["path"])
    assert alias.parent == control / "e" and alias.name.startswith("E")
    assert alias.read_bytes() == b"original evidence"
    assert References(tmp_path).file_path(str(path)) == str(alias)
    changed = store.put_bytes(b"new evidence", kind="fixture")
    assert refs.file_path(str(store.path_for_digest(changed.digest))) != str(alias)
    assert refs.get(shown["evidence_ref"])["digest"] == original.digest
    alias.unlink()
    alias.symlink_to(store.path_for_digest(changed.digest))
    with pytest.raises(WorkflowError, match="alias changed"):
        refs.file_path(str(path))
    assert store.read(original) == b"original evidence"


def test_legacy_config_does_not_rename_directories_or_expose_new_aliases(tmp_path):
    value = project_config().to_dict()
    value.pop("compact_paths")
    assert ProjectConfig.from_dict(value).compact_paths is False
    control = tmp_path / ".dpf"
    control.mkdir()
    (control / "project.json").write_text(json.dumps(value))
    store = ArtifactStore(control / "cas")
    artifact = store.put_bytes(b"legacy", kind="fixture")
    path = str(store.path_for_digest(artifact.digest))
    assert References(tmp_path).file_path(path) == path
