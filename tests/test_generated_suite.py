"""Generated entrypoints through local subprocess/real controller fixtures, not real drivers."""

import json
import os
import subprocess
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import patch

import pytest

from driver_port_factory.acquisition.repository import load_repository_acquisition
from driver_port_factory.core.models import WorkflowError
from driver_port_factory.migration import experiments, public_qemu
from driver_port_factory.migration.check_tools import check
from driver_port_factory.migration.contracts import MigrationStage as S
from driver_port_factory.migration.implementation_smoke import implementation_smoke
from driver_port_factory.platform import service, suite, worker
from tests.migration_support import smoke_fixture
from tests.test_managed_check_tools import active_job
from tests.test_translation_acceleration import active_project


def spec(command="true"):
    return {"devices": ["synthetic-device"], "steps": [{"guest_assert": command}]}


def registration_context(project, worktree):
    from contextlib import ExitStack

    stack = ExitStack()
    stack.enter_context(patch.object(service, "active"))
    stack.enter_context(patch.object(service, "verified"))
    stack.enter_context(patch.object(service, "locked", return_value=nullcontext()))
    stack.enter_context(patch.object(service, "load", return_value=({}, worktree)))
    return stack


def test_entrypoints_exist_before_cases_and_empty_or_failed_suite_never_passes(tmp_path):
    suite.install(tmp_path)
    before = {name: (tmp_path / ".dpf-output" / name).read_bytes() for name in suite.ENTRIES}
    env = os.environ | {"PYTHONPATH": str(Path("src").resolve())}
    for name in suite.ENTRIES:
        result = subprocess.run(
            [str(tmp_path / ".dpf-output" / name)],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 1 and "No registered cases" in result.stderr
    output = tmp_path / ".dpf-output"
    rows = []
    for name, command in (("a", "exit 1"), ("b", "echo ran > ran-b")):
        path = output / f"{name}.sh"
        path.write_text(command + "\n")
        rows.append({"id": name, "script": str(path.relative_to(tmp_path)), "timeout_seconds": 1})
    (output / "experiments.json").write_text(json.dumps(rows))
    assert suite.run(tmp_path) == 1
    assert (tmp_path / "ran-b").exists()  # Preserve remaining independent results after failure.
    (output / "a.sh").write_text("exit 0\n")
    assert suite.run(tmp_path) == 0
    (output / "a.sh").write_text("sleep 30\n")
    assert suite.run(tmp_path) == 1  # The declared per-case deadline is enforced.
    suite.install(tmp_path)
    assert all((output / n).read_bytes() == data for n, data in before.items())


def test_register_case_records_only_explicit_cases_and_preserves_other_entries(tmp_path):
    with (
        registration_context(None, tmp_path),
        patch.object(service, "command", return_value=["/bin/true"]),
    ):
        result = suite.register(None, "event", spec(), ["C1", "C2"])
        assert result["status"] == "REGISTERED_NOT_EXECUTED"
        assert not (tmp_path / ".dpf-output/qemu-runs").exists()
        suite.register(None, "shutdown", spec(), ["C3"])
        suite.register(None, "event", spec("false"), ["C1", "C2"])
        rows = experiments.cases(tmp_path)
        assert [r["id"] for r in rows] == ["event", "shutdown"]
        assert rows[0]["contracts"] == ["C1", "C2"]
        assert json.loads((tmp_path / result["case"]).read_text()) == spec("false")
        assert "dependencies" not in rows[0]  # Controller supplies normal case input selection.
        assert suite.generated(tmp_path, tmp_path / ".dpf-output/implementation-smoke.sh")
        snapshot = (tmp_path / ".dpf-output/experiments.json").read_bytes()
        with pytest.raises(WorkflowError):
            suite.register(None, "bad", {"steps": []}, ["C1"])
        assert (tmp_path / ".dpf-output/experiments.json").read_bytes() == snapshot


def test_generated_cases_reuse_worker_execution_and_bind_only_relevant_case_inputs(
    tmp_path,
):
    project, output = active_project(tmp_path)
    worktree = output.parent
    (worktree / "driver.rs").write_text("// synthetic driver\n")
    smoke_fixture(worktree)
    (output / "implementation-smoke.sh").unlink()  # Replace fixture with actual generated entry.
    helpers = output / "harness/platform"
    helpers.mkdir(parents=True)
    (helpers / "binding.json").write_text("{}")
    transport = helpers / "synthetic-case.py"
    transport.write_text(
        "import json,os,subprocess,sys\nfrom pathlib import Path\n"
        "c=json.loads(Path(sys.argv[1]).read_text())\n"
        "subprocess.run(['.dpf-output/qemu-system-smoke-fixture', '-kernel', "
        "os.environ['DPF_RUNTIME_ARTIFACT']], check=True)\n"
        "p=Path('.dpf-output/qemu-runs');p.mkdir(exist_ok=True)\n"
        "(p/(Path(sys.argv[1]).stem+'.log')).write_text('synthetic observation')\n"
        "sys.exit(1 if c['steps'][0]['guest_assert']=='false' else 0)\n"
    )
    import sys

    with (
        registration_context(project, worktree),
        patch.object(service, "command", return_value=[sys.executable, str(transport)]),
    ):
        suite.register(project, "a", spec(), ["C1"])
        suite.register(project, "b", spec(), ["C2"])
        job = active_job(project)
        assert "a: PASS; reused=False" in check(project, job, {"cases": ["a"]})
        assert "b: PASS; reused=False" in check(project, job, {"cases": ["b"]})
        arguments = {
            "attempt_dir": tmp_path / "capture",
            "worktree": worktree,
            "runtime_path": output / "runtime-artifact",
            "target_platform": "target",
        }
        with patch.object(public_qemu, "_run_public_harness") as execute:
            for entry in suite.ENTRIES:
                observed = public_qemu.run_public_harness(script_path=output / entry, **arguments)
                assert observed.passed and len(observed.case_results) == 2
            base = load_repository_acquisition(project).target_worktree.base_commit
            smoke = implementation_smoke(project, worktree, base)
            assert smoke["status"] == "PASS"
            assert len(smoke["execution"]["case_results"]) == 2
            execute.assert_not_called()
        old = {r["id"]: r["receipt"] for r in observed.case_results}
        suite.register(project, "a", spec("false"), ["C1"])
        updated = public_qemu.run_public_harness(script_path=output / "public-qemu.sh", **arguments)
        assert updated.passed
        current = {r["id"]: r for r in updated.case_results}
        assert current["a"]["receipt"] == old["a"] and current["a"]["status"] == "PASS"
        assert current["b"]["receipt"] == old["b"] and current["b"]["status"] == "PASS"
        (helpers / "shared-input.txt").write_text("changed shared test input")
        common = public_qemu.run_public_harness(script_path=output / "public-qemu.sh", **arguments)
        assert all(r["receipt"] == current[r["id"]]["receipt"] for r in common.case_results)
        assert project.stage(S.DRIVER_IMPLEMENTATION).status.value == "RUNNING"


def test_analysis_cannot_register_cases_and_custom_entrypoint_is_preserved(tmp_path):
    with (
        patch.object(worker, "authorize", return_value="target_platform_study"),
        patch.object(suite, "register") as register,
        pytest.raises(WorkflowError, match="implementation"),
    ):
        worker.run(
            None, "job", {"action": "register_case", "id": "a", "case": spec(), "contracts": ["C1"]}
        )
    register.assert_not_called()
    suite.install(tmp_path)
    smoke = tmp_path / ".dpf-output/implementation-smoke.sh"
    smoke.write_text("custom test\n")
    suite.install(tmp_path)
    assert smoke.read_text() == "custom test\n" and not suite.generated(tmp_path, smoke)


def test_final_suite_cannot_fill_a_failure_with_earlier_passing_case(tmp_path):
    project, output = active_project(tmp_path)
    tree = output.parent
    (tree / "driver.rs").write_text("// synthetic final driver\n")
    smoke_fixture(tree)
    harness = output / "harness"
    harness.mkdir(exist_ok=True)
    body = (output / "implementation-smoke.sh").read_text()
    rows = []
    for name in ("panic", "shutdown", "lifecycle"):
        (harness / f"{name}.sh").write_text(body)
        rows.append(
            {"id": name, "script": f".dpf-output/harness/{name}.sh", "timeout_seconds": 300}
        )
    (output / "experiments.json").write_text(json.dumps(rows))
    runtime = output / "runtime-artifact"
    assert experiments.run_suite(project, tree, runtime).passed
    (harness / "shutdown.sh").write_text(body + "exit 1\n")
    with patch.object(
        public_qemu, "_run_public_harness", wraps=public_qemu._run_public_harness
    ) as execute:
        final = experiments.run_suite(project, tree, runtime, final=True)
    assert execute.call_count == 3
    assert not final.passed
    assert [c["status"] for c in final.case_results] == ["PASS", "FAIL", "PASS"]
    assert all(not c["reused"] for c in final.case_results)
