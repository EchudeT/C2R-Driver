from __future__ import annotations

import json
import unittest
from types import SimpleNamespace

from driver_port_factory.core.models import WorkflowError
from driver_port_factory.migration.implementation import DriverImplementationGate
from driver_port_factory.target_study.contracts import TargetStudyArtifact


class ImplementationTargetChangeGateTests(unittest.TestCase):
    def test_non_driver_owned_plan_requires_a_real_target_change(self) -> None:
        plan = {
            "required_change_level": "target-api-framework",
            "driver_owned_paths": ["kernel/driver"],
            "proposed_preexisting_changes": [{"change_id": "target-api"}],
        }
        context = SimpleNamespace(
            one_dependency=lambda kind: (
                None,
                json.dumps(plan).encode(),
            )
            if kind is TargetStudyArtifact.CHANGE_PLAN
            else None
        )
        gate = object.__new__(DriverImplementationGate)
        gate.context = context
        gate.inventory = {"target_changes": []}

        with self.assertRaisesRegex(WorkflowError, "requires a pre-existing target change"):
            gate._target_changes({}, set())


if __name__ == "__main__":
    unittest.main()
