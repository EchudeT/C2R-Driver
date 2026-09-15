from __future__ import annotations

from dataclasses import dataclass

from ..codex.contracts import CodexArtifact
from ..core.contracts import ArtifactKey, StageKey
from ..core.models import (
    ActorRole,
    ArtifactRequirement,
    OutputCardinality,
    StageOwner,
    StageSpec,
)


@dataclass(frozen=True, slots=True)
class StageRow:
    name: StageKey
    description: str
    owner: StageOwner
    required_outputs: tuple[ArtifactKey, ...]
    auxiliary_outputs: tuple[ArtifactKey, ...] = ()
    repeatable_outputs: tuple[ArtifactKey, ...] = ()


def stage_spec(
    name: StageKey,
    description: str,
    owner: StageOwner,
    dependency: StageKey | None,
    outputs: tuple[ArtifactKey, ...],
    roles: tuple[ActorRole, ...],
    *,
    auxiliary_outputs: tuple[ArtifactKey, ...] = (),
    repeatable_outputs: tuple[ArtifactKey, ...] = (),
    accept_failed: bool = False,
) -> StageSpec:
    codex_evidence = (
        (CodexArtifact.JOB_RESULT, CodexArtifact.EVENT_LOG)
        if owner in {StageOwner.CODEX, StageOwner.HYBRID, StageOwner.INDEPENDENT}
        else ()
    )
    repeatable = {output.value for output in repeatable_outputs}
    declared = {output.value for output in outputs}
    unknown_repeatable = sorted(repeatable - declared)
    if unknown_repeatable:
        raise ValueError(
            "repeatable outputs are not required by the stage: " + ", ".join(unknown_repeatable)
        )
    requirements = tuple(
        ArtifactRequirement(
            output,
            OutputCardinality.ONE_OR_MORE
            if output.value in repeatable
            else OutputCardinality.EXACTLY_ONE,
        )
        for output in outputs
    )
    return StageSpec(
        name=name,
        description=description,
        owner=owner,
        dependencies=(dependency,) if dependency else (),
        required_outputs=requirements,
        auxiliary_outputs=(*auxiliary_outputs, *codex_evidence),
        allowed_roles=roles,
        accept_failed_dependencies=accept_failed,
    )


def append_linear(
    specs: list[StageSpec],
    rows: tuple[StageRow, ...],
    dependency: StageKey,
    roles: tuple[ActorRole, ...],
) -> StageKey:
    previous = dependency
    for row in rows:
        specs.append(
            stage_spec(
                row.name,
                row.description,
                row.owner,
                previous,
                row.required_outputs,
                roles,
                auxiliary_outputs=row.auxiliary_outputs,
                repeatable_outputs=row.repeatable_outputs,
            )
        )
        previous = row.name
    return previous
