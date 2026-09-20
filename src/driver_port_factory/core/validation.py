from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Protocol

from .contracts import ArtifactKey, StageKey
from .models import ArtifactRef, WorkflowError


class ArtifactValidator(Protocol):
    def __call__(self, data: bytes) -> None: ...


class ArtifactInputs(Sequence):
    """Read and verify only the dependency payloads a validator actually uses."""

    def __init__(self, refs: Iterable[ArtifactRef], reader: Callable[[ArtifactRef], bytes]):
        self.refs = tuple(refs)
        self.reader = reader

    def __len__(self) -> int:
        return len(self.refs)

    def __getitem__(self, index):
        if isinstance(index, slice):
            return tuple((ref, self.reader(ref)) for ref in self.refs[index])
        ref = self.refs[index]
        return ref, self.reader(ref)

    def matching(self, kind: ArtifactKey) -> list[tuple[ArtifactRef, bytes]]:
        return [(ref, self.reader(ref)) for ref in self.refs if ref.kind == kind.value]


@dataclass(frozen=True, slots=True)
class BundleValidationContext:
    project_root: Path
    artifacts: tuple[tuple[ArtifactRef, bytes], ...]
    dependency_artifacts: Sequence[tuple[ArtifactRef, bytes]]
    current_stage_artifacts: Sequence[tuple[ArtifactRef, bytes]] = ()

    def one_current(self, kind: ArtifactKey) -> tuple[ArtifactRef, bytes]:
        return self._one(self.artifacts, kind, "final bundle")

    def one_dependency(self, kind: ArtifactKey) -> tuple[ArtifactRef, bytes]:
        return self._one(self.dependency_artifacts, kind, "dependency bundle")

    def one_auxiliary(self, kind: ArtifactKey) -> tuple[ArtifactRef, bytes]:
        return self._one(self.current_stage_artifacts, kind, "auxiliary bundle")

    @staticmethod
    def _one(
        artifacts: Sequence[tuple[ArtifactRef, bytes]],
        kind: ArtifactKey,
        label: str,
    ) -> tuple[ArtifactRef, bytes]:
        matches = (artifacts.matching(kind) if isinstance(artifacts, ArtifactInputs) else
                   [artifact for artifact in artifacts if artifact[0].kind == kind.value])
        if len(matches) != 1:
            raise WorkflowError(f"{label} requires exactly one {kind.value}, found {len(matches)}")
        return matches[0]


class BundleValidator(Protocol):
    def __call__(self, context: BundleValidationContext) -> None: ...


@dataclass(frozen=True, slots=True)
class ValidationRegistry:
    """Immutable validator dispatch assembled from bounded-context contracts."""

    _artifacts: Mapping[str, ArtifactKey]
    _validators: Mapping[str, ArtifactValidator]
    _bundle_validators: Mapping[str, BundleValidator]

    @classmethod
    def compose(
        cls,
        validator_groups: Iterable[Mapping[ArtifactKey, ArtifactValidator]],
        bundle_groups: Iterable[Mapping[StageKey, BundleValidator]] = (),
    ) -> ValidationRegistry:
        artifacts: dict[str, ArtifactKey] = {}
        validators: dict[str, ArtifactValidator] = {}
        for group in validator_groups:
            for key, validator in group.items():
                if key.value in artifacts:
                    raise ValueError(f"duplicate artifact validator: {key.value}")
                artifacts[key.value] = key
                validators[key.value] = validator
        bundles: dict[str, BundleValidator] = {}
        for group in bundle_groups:
            for stage, validator in group.items():
                if stage.value in bundles:
                    raise ValueError(f"duplicate bundle validator: {stage.value}")
                bundles[stage.value] = validator
        return cls(
            MappingProxyType(artifacts),
            MappingProxyType(validators),
            MappingProxyType(bundles),
        )

    def parse(self, value: str) -> ArtifactKey:
        try:
            return self._artifacts[value]
        except KeyError as error:
            raise WorkflowError(f"unknown artifact kind: {value}") from error

    def validate(self, kind: ArtifactKey, data: bytes) -> None:
        registered = self._artifacts.get(kind.value)
        if registered is not kind:
            raise WorkflowError(f"unregistered artifact kind: {kind.value}")
        self._validators[kind.value](data)

    def validate_bundle(
        self,
        stage: StageKey,
        context: BundleValidationContext,
    ) -> None:
        validator = self._bundle_validators.get(stage.value)
        if validator is not None:
            validator(context)

    def contains(self, kind: ArtifactKey) -> bool:
        return self._artifacts.get(kind.value) is kind

    def has_bundle_validator(self, stage: StageKey) -> bool:
        return stage.value in self._bundle_validators

    def values(self) -> tuple[str, ...]:
        return tuple(self._artifacts)


def json_value(data: bytes, label: str) -> Any:
    try:
        return json.loads(data)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise WorkflowError(f"{label} must be a UTF-8 JSON document") from error


def json_object(data: bytes, label: str) -> dict[str, Any]:
    value = json_value(data, label)
    if not isinstance(value, dict):
        raise WorkflowError(f"{label} must be a JSON object")
    return value


def require_fields(value: Mapping[str, Any], fields: Iterable[str], label: str) -> None:
    missing = sorted(set(fields) - value.keys())
    if missing:
        raise WorkflowError(f"{label} missing fields: {', '.join(missing)}")


def nonempty(data: bytes) -> None:
    if not data:
        raise WorkflowError("artifact must not be empty")


def json_object_document(data: bytes) -> None:
    json_object(data, "artifact")


def utf8_document(data: bytes) -> None:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise WorkflowError("artifact must be UTF-8") from error
    if not text.strip():
        raise WorkflowError("artifact must not be blank")
