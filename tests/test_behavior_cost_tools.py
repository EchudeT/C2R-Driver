"""Offline work-packet/scaffold/transport regressions; no model or real driver claims."""

import json
import tomllib
from pathlib import Path
from unittest.mock import patch

import pytest

from driver_port_factory.codex.behavior_prompt import focused_context
from driver_port_factory.core.models import WorkflowError
from driver_port_factory.migration.contracts import MigrationStage as S
from driver_port_factory.platform import guest, scaffold, service, worker
from tests.test_platform_execution import fake_qemu


def component_fixture(root):
    files = {
        "Cargo.toml": '[workspace]\nmembers=["comps/example","owner"]\n'
        'default-members=["comps/example"]\n[workspace.package]\nedition="2024"\n'
        '[workspace.lints.rust]\nunsafe_code="deny"\n[workspace.dependencies]\n'
        'example={path="comps/example"}\ncomponent="1"\nostd="1"\n',
        "Components.toml": '[components]\nexample={name="example"}\n',
        "comps/example/Cargo.toml": '[package]\nname="example"\n[dependencies]\n'
        "component.workspace=true\n",
        "owner/Cargo.toml": '[package]\nname="owner"\n[dependencies]\nexample.workspace=true\n',
        "owner/src/lib.rs": "#![no_std]\nuse example as _;\n",
    }
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return {root / name: text for name, text in files.items()}


def test_scaffold_wires_workspace_component_and_actual_owner_without_driver_answer(tmp_path):
    component_fixture(tmp_path)
    before, changes = scaffold.plan(tmp_path, "new-device", "example", ["ostd"], "owner/src/lib.rs")
    scaffold.apply(before, changes)
    manifest = tomllib.loads((tmp_path / "Cargo.toml").read_text())["workspace"]
    assert "comps/new-device" in manifest["members"]
    assert "comps/new-device" in manifest["default-members"]
    assert manifest["dependencies"]["new-device"] == {"path": "comps/new-device"}
    components = tomllib.loads((tmp_path / "Components.toml").read_text())["components"]
    assert components["new-device"] == {"name": "new-device"}
    owner = tomllib.loads((tmp_path / "owner/Cargo.toml").read_text())
    assert owner["dependencies"]["new-device"] == {"workspace": True}
    assert "use new_device as _;" in (tmp_path / "owner/src/lib.rs").read_text()
    crate = tomllib.loads((tmp_path / "comps/new-device/Cargo.toml").read_text())
    assert set(crate["dependencies"]) == {"component", "ostd"}
    code = (tmp_path / "comps/new-device/src/lib.rs").read_text()
    assert "todo!" in code and "init_component" in code and "Ok(())" not in code
    current = {p: p.read_bytes() for p in changes}
    with pytest.raises(WorkflowError, match="already exists"):
        scaffold.plan(tmp_path, "new-device", "example", [], "owner/src/lib.rs")
    assert all(p.read_bytes() == text for p, text in current.items())


def test_scaffold_layout_or_write_failure_preserves_existing_files(tmp_path):
    original = component_fixture(tmp_path)
    with pytest.raises(WorkflowError, match="unknown"):
        scaffold.plan(tmp_path, "new-device", "example", ["missing"], "owner/src/lib.rs")
    assert all(p.read_text() == text for p, text in original.items())
    before, changes = scaffold.plan(tmp_path, "new-device", "example", [], "owner/src/lib.rs")
    write = Path.write_text

    def fail_new_file(path, text, *args, **kwargs):
        if path == tmp_path / "comps/new-device/Cargo.toml":
            raise OSError("synthetic full disk")
        return write(path, text, *args, **kwargs)

    with patch.object(Path, "write_text", fail_new_file), pytest.raises(OSError, match="full disk"):
        scaffold.apply(before, changes)
    assert all(p.read_text() == text for p, text in original.items())
    assert not (tmp_path / "comps/new-device").exists()
    scaffold.apply(before, changes)  # A failed write must not prevent the next attempt.


def test_scaffold_rejects_escape_without_writing(tmp_path):
    original = component_fixture(tmp_path)
    with pytest.raises(WorkflowError, match="stay in"):
        scaffold.plan(tmp_path, "new-device", "example", [], "../outside.rs")
    assert all(p.read_text() == text for p, text in original.items())


def test_work_packet_interns_exact_prose_without_mutating_plan_or_repair(tmp_path):
    shared = "ONE-SHARED-OBLIGATION: retain resources until callbacks cease."
    context = {
        "behavior_progress": {
            "current": {"id": "B1", "outcome": shared},
            "route_context": {
                "contracts": [{"id": "C1", "text": shared}],
                "premises": [{"id": "P1", "text": "distinct prerequisite"}],
            },
        },
        "repair_execution": {"defect": "must remain visible"},
        "delivery_essentials": {"source": "analysis.md", "text": "whole report"},
    }
    snapshot = json.dumps(context)
    result = focused_context(S.DRIVER_IMPLEMENTATION, context)
    assert json.dumps(context) == snapshot
    assert json.dumps(result).count(shared) == 1
    progress = result["behavior_progress"]
    assert progress["current"]["id"] == "B1"
    assert progress["current"]["outcome"] == progress["route_context"]["contracts"][0]["text"]
    assert "distinct prerequisite" in progress["passages"].values()
    assert result["repair_execution"] == context["repair_execution"]
    assert result["delivery_essentials"]["source"] == "analysis.md"
    final = {"behavior_progress": {"current": None, "plan_exists": True}}
    assert focused_context(S.DRIVER_IMPLEMENTATION, final) == final


@pytest.mark.parametrize("mode", ["pass", "wrong-data", "reuse", "wrong-return", "bool-as-int"])
def test_local_qmp_transport_checks_fields_and_cannot_reuse_events(tmp_path, mode):
    qemu = fake_qemu(tmp_path)
    script = qemu.read_text().replace("'return':{}", "'return':{'running':True,'status':'running'}")
    script = script.replace('"event":"READY"', '"event":"READY","data":{"count":1,"extra":2}')
    qemu.write_text(script)
    steps = [
        {"expect_event": {"event": "READY", "data": {"count": 2 if mode == "wrong-data" else 1}}},
        {"qmp_assert": {"execute": "query-status", "match": {"running": True}}},
    ]
    if mode == "reuse":
        steps.append({"wait_event": "READY"})
    if mode in {"wrong-return", "bool-as-int"}:
        steps[-1]["qmp_assert"]["match"] = {"running": False if mode == "wrong-return" else 1}
    result = guest.run(
        {"qemu": str(qemu), "qemu_args": []},
        "synthetic.iso",
        {"steps": steps, "timeout_seconds": 1},
        tmp_path / "output",
    )
    assert (result["status"] == "PASS") is (mode == "pass")
    if mode in {"wrong-return", "bool-as-int"}:
        assert "QMP_ASSERT_FAILED" in result["error"]
    assert '"count":1' in (tmp_path / "output/qmp.jsonl").read_text()


def test_inline_case_preserves_stimulus_assertions_and_uses_normal_service(tmp_path):
    spec = {
        "devices": ["synthetic-device,property=0"],
        "steps": [{"expect_event": {"event": "SYNTHETIC", "data": {"value": 7}}}],
    }

    def execute(project, path):
        assert json.loads(path.read_text()) == spec
        return {"status": "CASE_OBSERVED", "receipt": "original receipt"}

    with (
        patch.object(service, "load", return_value=({}, tmp_path)),
        patch.object(service, "run_case", side_effect=execute) as run,
    ):
        result = worker.run_case(None, spec)
        assert result["receipt"] == "original receipt"
        assert Path(result["case"]).is_file()
        with pytest.raises(WorkflowError):
            worker.run_case(None, {"steps": [{"qmp_assert": {"execute": "query-status"}}]})
        run.assert_called_once()
        assert len(list(tmp_path.rglob("case-*.json"))) == 1


def test_analysis_cannot_scaffold_target(tmp_path):
    with (
        patch.object(worker, "authorize", return_value="target_platform_study"),
        patch.object(scaffold, "run") as write,
        pytest.raises(WorkflowError, match="implementation"),
    ):
        worker.run(None, "job", {"action": "scaffold", "package": "new", "template": "example"})
    write.assert_not_called()
