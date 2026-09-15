from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class TestDisposition(StrEnum):
    PORTABLE = "PORTABLE"
    ADAPTABLE = "ADAPTABLE"
    SOURCE_INTERNAL = "SOURCE_INTERNAL"
    UNSUPPORTED = "UNSUPPORTED"


@dataclass(frozen=True, slots=True)
class Scenario:
    scenario_id: str
    device_class: str
    requirement_ids: tuple[str, ...]
    stimulus: Mapping[str, Any]
    expected: Mapping[str, Any]
    disposition: TestDisposition = TestDisposition.PORTABLE
    source_test_refs: tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Observation:
    scenario_id: str
    backend: str
    values: Mapping[str, Any]
    raw_artifact_digest: str
