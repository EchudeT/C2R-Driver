"""Offline budget guard: stop before paid work, retain accounting and controller state."""

from unittest.mock import patch

import pytest

from driver_port_factory.composition import open_project
from driver_port_factory.control.budget import ModelBudgetStopped, configure, guard, read
from driver_port_factory.core.models import ControllerError, StageStatus
from driver_port_factory.migration.contracts import MigrationStage as S
from tests.workflow_support import ready_implementation, runner


def test_budget_persists_and_stops_unknown_or_exhausted_cost_before_gateway(tmp_path):
    project = ready_implementation(tmp_path)
    configure(project, 10)
    configure(project, None)
    configure(project, 10)
    project = open_project(project.root)
    assert read(project) == 10
    with pytest.raises(ControllerError, match="persisted"):
        configure(project, 11)
    for amount in (0, -1, float("nan"), float("inf"), True):
        with pytest.raises(ControllerError, match="finite positive"):
            configure(project, amount)
    for total in (
        {"usd": 10, "unknown_usage_calls": 0, "unpriced_calls": 0},
        {"usd": 3, "unknown_usage_calls": 1, "unpriced_calls": 1},
        {"usd": 11, "unknown_usage_calls": 0, "unpriced_calls": 0},
    ):
        with (
            patch(
                "driver_port_factory.control.statistics.project_statistics",
                return_value={"totals": total},
            ),
            patch("driver_port_factory.codex.cli.CodexExecGateway.run") as model,
            pytest.raises(ModelBudgetStopped),
        ):
            runner(project)._run_project(project)
        model.assert_not_called()
        assert project.stage(S.DRIVER_IMPLEMENTATION).status is StageStatus.READY
        assert not list((project.control / "checker-decisions").glob("*.pending"))
    with patch(
        "driver_port_factory.control.statistics.project_statistics",
        return_value={"totals": {"usd": 9.99, "unknown_usage_calls": 0, "unpriced_calls": 0}},
    ):
        guard(project)  # Permission for a next call, not a prediction of that call's price.


def test_no_budget_keeps_existing_runs_unchanged(tmp_path):
    project = ready_implementation(tmp_path)
    with patch("driver_port_factory.control.statistics.project_statistics") as statistics:
        guard(project)
    statistics.assert_not_called()
