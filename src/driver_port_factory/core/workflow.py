from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType

from .contracts import ArtifactKey, StageKey
from .models import ArtifactRequirement, OutputCardinality, StageSpec, WorkflowError
from .validation import ValidationRegistry


@dataclass(frozen=True, slots=True)
class StageOutputContract:
    stage: StageKey
    required: Mapping[str, ArtifactRequirement]
    auxiliary: Mapping[str, ArtifactKey]

    def require_auxiliary(self, value: str) -> ArtifactKey:
        if value in self.required:
            raise WorkflowError(
                f"required output {value} for stage {self.stage.value} "
                "may only be written by complete finalization"
            )
        try:
            return self.auxiliary[value]
        except KeyError as error:
            raise WorkflowError(
                f"output {value} is not declared for stage {self.stage.value}"
            ) from error

    def validate_final_bundle(self, values: Iterable[str]) -> None:
        counts = Counter(values)
        unexpected = sorted(set(counts) - set(self.required) - set(self.auxiliary))
        cardinality_errors = self._cardinality_errors(counts)
        if cardinality_errors or unexpected:
            details = []
            if cardinality_errors:
                details.extend(cardinality_errors)
            if unexpected:
                details.append("unexpected: " + ", ".join(unexpected))
            raise WorkflowError(
                f"invalid final output bundle for stage {self.stage.value}: " + "; ".join(details)
            )

    def validate_existing_auxiliary(self, values: Iterable[str]) -> None:
        actual = set(values)
        invalid = sorted(actual - set(self.auxiliary))
        if invalid:
            raise WorkflowError(
                f"stage {self.stage.value} has invalid pre-finalization outputs: "
                + ", ".join(invalid)
            )

    def validate_persisted(self, values: Iterable[str]) -> None:
        counts = Counter(values)
        actual = set(counts)
        allowed = set(self.required) | set(self.auxiliary)
        unexpected = sorted(actual - allowed)
        if self._cardinality_errors(counts) or unexpected:
            raise WorkflowError(f"persisted outputs violate stage {self.stage.value} contract")

    def _cardinality_errors(self, counts: Mapping[str, int]) -> list[str]:
        errors: list[str] = []
        for value, requirement in self.required.items():
            count = counts.get(value, 0)
            if requirement.cardinality is OutputCardinality.EXACTLY_ONE and count != 1:
                errors.append(f"{value}: expected exactly one, found {count}")
            elif requirement.cardinality is OutputCardinality.ONE_OR_MORE and count < 1:
                errors.append(f"{value}: expected one or more, found {count}")
        return errors


@dataclass(frozen=True, slots=True)
class WorkflowDefinition:
    stages: tuple[StageSpec, ...]
    _stage_index: Mapping[str, StageKey]
    _spec_index: Mapping[str, StageSpec]
    _output_contracts: Mapping[str, StageOutputContract]

    def descendants(self, name: StageKey) -> set[str]:
        affected = {name.value}
        while True:
            following = affected | {s.name.value for s in self.stages
                if any(d.value in affected for d in s.dependencies)}
            if following == affected:
                return affected
            affected = following

    @classmethod
    def build(
        cls,
        stages: tuple[StageSpec, ...],
        validators: ValidationRegistry,
    ) -> WorkflowDefinition:
        stage_index: dict[str, StageKey] = {}
        output_contracts: dict[str, StageOutputContract] = {}
        for stage in stages:
            if stage.name.value in stage_index:
                raise WorkflowError(f"duplicate workflow stage: {stage.name.value}")
            stage_index[stage.name.value] = stage.name
            required = {output.value: output for output in stage.required_outputs}
            auxiliary = {output.value: output for output in stage.auxiliary_outputs}
            if len(required) != len(stage.required_outputs):
                raise WorkflowError(f"stage {stage.name.value} repeats a required output")
            if len(auxiliary) != len(stage.auxiliary_outputs):
                raise WorkflowError(f"stage {stage.name.value} repeats an auxiliary output")
            overlap = sorted(set(required) & set(auxiliary))
            if overlap:
                raise WorkflowError(
                    f"stage {stage.name.value} declares outputs as required and auxiliary: "
                    + ", ".join(overlap)
                )
            for output in stage.required_outputs:
                if not validators.contains(output.kind):
                    raise WorkflowError(
                        f"stage {stage.name.value} has no validator for {output.value}"
                    )
            for output in stage.auxiliary_outputs:
                if not validators.contains(output):
                    raise WorkflowError(
                        f"stage {stage.name.value} has no validator for {output.value}"
                    )
            output_contracts[stage.name.value] = StageOutputContract(
                stage.name,
                MappingProxyType(required),
                MappingProxyType(auxiliary),
            )
        known = set(stage_index)
        for stage in stages:
            unknown = [item.value for item in stage.dependencies if item.value not in known]
            if unknown:
                raise WorkflowError(
                    f"stage {stage.name.value} has unknown dependencies: {', '.join(unknown)}"
                )
        return cls(
            stages,
            MappingProxyType(stage_index),
            MappingProxyType({stage.name.value: stage for stage in stages}),
            MappingProxyType(output_contracts),
        )

    def parse_stage(self, value: str) -> StageKey:
        try:
            return self._stage_index[value]
        except KeyError as error:
            raise WorkflowError(f"unknown stage: {value}") from error

    def output_contract(self, stage: StageKey) -> StageOutputContract:
        try:
            return self._output_contracts[stage.value]
        except KeyError as error:
            raise WorkflowError(f"stage is not in this workflow: {stage.value}") from error

    @property
    def stage_values(self) -> tuple[str, ...]:
        return tuple(self._stage_index)

    def spec(self, stage: StageKey) -> StageSpec:
        try:
            return self._spec_index[stage.value]
        except KeyError as error:
            raise WorkflowError(f"stage is not in this workflow: {stage.value}") from error


@dataclass(frozen=True, slots=True)
class StageCatalog:
    """All stage identities admitted by the installed domain workflows."""

    _stages: Mapping[str, StageKey]

    @classmethod
    def compose(cls, groups: Iterable[Iterable[StageKey]]) -> StageCatalog:
        stages: dict[str, StageKey] = {}
        for group in groups:
            for stage in group:
                registered = stages.get(stage.value)
                if registered is not None and registered is not stage:
                    raise ValueError(f"duplicate stage identity: {stage.value}")
                stages[stage.value] = stage
        return cls(MappingProxyType(stages))

    def contains(self, value: str) -> bool:
        return value in self._stages
