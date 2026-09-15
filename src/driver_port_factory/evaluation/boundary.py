from __future__ import annotations

from dataclasses import dataclass

from ..core.models import ActorRole, EvaluationMode, WorkflowError


@dataclass(frozen=True, slots=True)
class EvaluationBinding:
    mode: EvaluationMode
    actor_role: ActorRole
    workspace_domain: str
    candidate_digest: str | None
    public_bundle_digest: str | None
    private_commitment: str | None
    candidate_contents_seen_before_freeze: bool = False


def verify_binding(binding: EvaluationBinding) -> None:
    if binding.mode is EvaluationMode.DEVELOPER_EVIDENCE:
        return
    if binding.actor_role is ActorRole.CURATOR:
        if binding.mode is EvaluationMode.POST_HOC_SEALED_BLIND:
            if not binding.candidate_digest:
                raise WorkflowError("post-hoc curator must first accept an opaque candidate digest")
            if binding.candidate_contents_seen_before_freeze:
                raise WorkflowError(
                    "post-hoc curator saw candidate contents before private-test freeze"
                )
    elif (
        binding.actor_role is ActorRole.MIGRATION_OPERATOR
        and binding.mode is EvaluationMode.PROSPECTIVE_BLIND
        and (not binding.public_bundle_digest or not binding.private_commitment)
    ):
        raise WorkflowError("prospective migrator requires public bundle and curator commitment")
    elif binding.actor_role is ActorRole.EVALUATOR:
        if not binding.candidate_digest or not binding.private_commitment:
            raise WorkflowError("evaluator requires sealed candidate digest and private commitment")
    else:
        raise WorkflowError(f"role {binding.actor_role.value} is not valid for blind execution")
