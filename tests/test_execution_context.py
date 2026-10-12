"""Offline context handoff checks; no model calls or driver-validation claims."""

import json
from copy import deepcopy
from types import SimpleNamespace

from driver_port_factory.codex.execution_context import compact_material
from driver_port_factory.core.artifacts import ArtifactStore
from driver_port_factory.migration.contracts import MigrationStage as S


def fixture(tmp_path):
    project = SimpleNamespace(artifacts=ArtifactStore(tmp_path / "cas"))
    context = {
        "tool_runtime": {"platform_execution": {"manual": "tool usage " * 300}},
        "prepared_public_tests": {
            "cases": ["public-1", "public-2"],
            "interface": "INTERFACE.md",
            "rule": "fixed complete final acceptance " * 80,
        },
        "behavior_progress": {
            "current": {"id": "B1", "contracts": ["C1"]},
            "route_context": {
                "contracts": [{"id": "C1", "sources": [{"path": "driver.c"}]}],
                "premises": [{"id": "P1", "status": "supported"}],
            },
        },
        "controller_feedback": "Repair the concrete recorded failure",
    }
    return project, context


def test_repeat_indexes_only_stable_manuals_and_retains_current_semantic_inputs(tmp_path):
    project, context = fixture(tmp_path)
    original = deepcopy(context)
    first, known = compact_material(project, S.DRIVER_IMPLEMENTATION, context, {})
    assert first == original
    second, identities = compact_material(project, S.DRIVER_IMPLEMENTATION, context, known)
    assert context == original and identities == known
    for indexed, full in [
        (
            second["tool_runtime"]["platform_execution"],
            context["tool_runtime"]["platform_execution"],
        ),
        (second["prepared_public_tests"], context["prepared_public_tests"]),
    ]:
        path = project.artifacts.path_for_digest(indexed["source"]["digest"])
        assert json.loads(path.read_text()) == full
    assert second["prepared_public_tests"]["cases"] == ["public-1", "public-2"]
    assert second["behavior_progress"] == original["behavior_progress"]
    assert second["controller_feedback"] == original["controller_feedback"]


def test_changed_manual_and_reset_session_receive_full_material(tmp_path):
    project, context = fixture(tmp_path)
    _, known = compact_material(project, S.DRIVER_IMPLEMENTATION, context, {})
    context["tool_runtime"]["platform_execution"]["manual"] += "changed calling convention"
    updated, _ = compact_material(project, S.DRIVER_IMPLEMENTATION, context, known)
    assert updated["tool_runtime"] == context["tool_runtime"]
    assert "source" in updated["prepared_public_tests"]
    # New sessions and native compaction clear these identities in the existing caller.
    reset, _ = compact_material(project, S.DRIVER_IMPLEMENTATION, context, {})
    assert reset == context


def test_other_phases_and_completed_plan_are_not_compacted(tmp_path):
    project, context = fixture(tmp_path)
    assert compact_material(project, S.PUBLIC_QEMU_VALIDATION, context, {}) == (context, {})
    context["behavior_progress"] = {"current": None, "plan_exists": True}
    assert compact_material(project, S.DRIVER_IMPLEMENTATION, context, {}) == (context, {})


def test_native_compaction_clears_manual_identity_before_next_handoff(tmp_path, monkeypatch):
    from driver_port_factory.codex.compaction import CURSOR, refresh_inputs
    from tests.test_compaction_refresh import THREAD, rollout

    project, context = fixture(tmp_path)
    log = rollout(tmp_path / "model-home", monkeypatch)
    _, known = compact_material(project, S.DRIVER_IMPLEMENTATION, context, {})
    _, cursor, _ = refresh_inputs(THREAD, known)
    known[CURSOR] = cursor
    log.write_text(log.read_text() + '{"type":"compacted","payload":{"message":"summary"}}\n')
    fresh, _, observation = refresh_inputs(THREAD, known)
    assert observation["reinject"]
    recovered, _ = compact_material(project, S.DRIVER_IMPLEMENTATION, context, fresh)
    assert recovered == context
