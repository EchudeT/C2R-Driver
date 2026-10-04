"""Offline oracle integrity / transport boundaries, not real driver validation."""

import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from driver_port_factory.core.models import WorkflowError
from driver_port_factory.migration import experiments
from driver_port_factory.platform import guest, public_tests, suite
from tests.test_platform_execution import fake_qemu


def project_at(root):
    control = root / ".dpf"
    control.mkdir()
    return SimpleNamespace(
        root=root,
        control=control,
        config=SimpleNamespace(
            managed_platform=True,
            source_platform="linux",
            target_platform="asterinas",
            driver_name="pvpanic-pci",
            behavior_scope=None,
        ),
    )


def test_prepared_public_cases_install_before_worker_and_cannot_be_weakened(tmp_path):
    project = project_at(tmp_path)
    worktree = tmp_path / "work"
    suite.install(worktree)
    public_tests.install(project, worktree)
    public_tests.install(project, worktree)
    manifest = worktree / ".dpf-output/experiments.json"
    original = manifest.read_text()
    rows = json.loads(original)
    with patch.object(experiments, "project_for", return_value=project):
        assert len(experiments.cases(worktree)) == 3
        # Removing a test must not silently shrink acceptance, including deleting the list.
        manifest.write_text(json.dumps(rows[:1]))
        with pytest.raises(WorkflowError, match="case set changed"):
            experiments.cases(worktree)
        manifest.unlink()
        with pytest.raises(WorkflowError, match="case set changed"):
            experiments.cases(worktree)
        manifest.write_text(original)
        path = worktree / public_tests.DIRECTORY / "pvpanic-native-panic.json"
        content = path.read_text()
        spec = json.loads(content)
        spec["steps"] = [{"wait_serial": "# "}]
        path.write_text(json.dumps(spec))
        with pytest.raises(WorkflowError, match="assertion changed"):
            experiments.cases(worktree)
        path.write_text(content)
        assert len(experiments.cases(worktree)) == 3
    # Oracle changes after installation require a new experiment, not a new passing receipt.
    with (
        patch.object(public_tests, "digest", return_value="changed"),
        pytest.raises(WorkflowError, match="definition"),
    ):
        public_tests.verify(project, worktree)


@pytest.mark.parametrize("count", [0, 1, 2])
def test_exact_event_counts_reject_missing_and_duplicate_observations(tmp_path, count):
    qemu = fake_qemu(tmp_path)
    text = qemu.read_text().replace(
        'f.write(b\'{"event":"READY"}\\n\')',
        'f.write(b\'{"event":"READY"}\\n\' * ' + str(count) + ")",
    )
    qemu.write_text(text)
    result = guest.run(
        {"qemu": str(qemu), "qemu_args": []},
        "synthetic.iso",
        {
            "timeout_seconds": 2,
            "steps": [
                {"wait_serial": "# "},
                {"observe_seconds": 0.1},
                {"assert_event_counts": {"READY": 1, "UNEXPECTED": 0}},
            ],
        },
        tmp_path / "output",
    )
    assert (result["status"] == "PASS") == (count == 1)
    if count != 1:
        assert "EVENT_COUNTS" in result["error"]


def test_subset_or_other_driver_does_not_inherit_native_pvpanic_requirements(tmp_path):
    project = project_at(tmp_path)
    project.config.driver_name = "different-driver"
    public_tests.install(project, tmp_path / "untouched")
    assert not (tmp_path / "untouched").exists()
    project.config.driver_name = "pvpanic-pci"
    project.config.behavior_scope = {
        "mode": "explicit-subset",
        "integration": "callback-harness",
        "required": ["one callback"],
        "excluded": [],
    }
    assert public_tests.context(project) is None


def test_prepared_suite_rejects_added_requirements(tmp_path):
    project = project_at(tmp_path)
    worktree = tmp_path / "work"
    suite.install(worktree)
    public_tests.install(project, worktree)
    manifest = worktree / ".dpf-output/experiments.json"
    rows = json.loads(manifest.read_text())
    manifest.write_text(json.dumps(rows + [{**rows[0], "id": "unrequested-ktest"}]))
    with pytest.raises(WorkflowError, match="case set changed"):
        public_tests.verify(project, worktree)
    with pytest.raises(WorkflowError, match="operator-prepared"):
        suite.register(project, "unrequested-ktest", {}, ["C1"])
