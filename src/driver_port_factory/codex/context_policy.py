"""Opt-in context rotation at a frozen implementation boundary, never a quality gate."""

import json

from ..core.events import RunEvent
from ..core.models import ArtifactDirection, EvaluationMode, StageStatus, WorkflowError, utc_now
from ..migration.contracts import MigrationArtifact, MigrationStage
from .context_reset import reset_session
from .sessions import read_session

POLICIES = ("persistent", "implementation-handoff")
POLICY_VERSION = 1


def read_policy(project) -> dict:
    path = project.control / "codex" / "context-policy.json"
    if not path.is_file():
        return {"name": "persistent", "version": POLICY_VERSION}
    value = json.loads(path.read_text())
    if value.get("name") not in POLICIES or value.get("version") != POLICY_VERSION:
        raise WorkflowError("unsupported context policy configuration")
    return value


def configure_policy(project, name: str, *, reason: str) -> dict:
    """Caller holds the controller lock. Repeating the same setting is a no-op."""
    if name not in POLICIES or not reason.strip():
        raise WorkflowError("context policy needs a supported name and a nonempty reason")
    if (name != "persistent"
            and project.config.evaluation_mode is not EvaluationMode.DEVELOPER_EVIDENCE):
        raise WorkflowError("context handoff policy is limited to developer-evidence runs")
    path = project.control / "codex" / "context-policy.json"
    current = read_policy(project)
    if path.is_file() and current["name"] == name:
        return current
    value = {"name": name, "version": POLICY_VERSION, "reason": reason,
             "configured_at": utc_now()}
    project.record_event(RunEvent.CONTEXT_POLICY, value)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value) + "\n")
    temporary.replace(path)
    return value


def _boundary_reason(project, stage, session, *, continuing: bool) -> str:
    if project.config.evaluation_mode is not EvaluationMode.DEVELOPER_EVIDENCE:
        return "ineligible_evaluation_mode"
    if stage is not MigrationStage.DRIVER_IMPLEMENTATION:
        return "outside_implementation_boundary"
    if continuing:
        return "continuation_preserved"
    # A reset is only eligible before the first invocation, across all sessions.
    # This also prevents a late opt-in from resetting an ongoing implementation.
    if any((project.control / "codex").glob("driver_implementation-*.metrics.json")):
        return "implementation_already_started"
    if not session.get("thread_id"):
        return "already_fresh_or_handoff_pending"
    if session.get("automatic_boundary"):
        return "boundary_already_consumed"
    if project.stage(MigrationStage.CONTRACTS).status is not StageStatus.PASS:
        return "contracts_not_frozen"
    required = {MigrationArtifact.CONTRACTS.value, MigrationArtifact.TEST_PORT_MATRIX.value}
    refs = project.current_artifact_refs(
        stage=MigrationStage.CONTRACTS, direction=ArtifactDirection.OUTPUT)
    present = {ref.kind for ref in refs
               if project.artifacts.path_for_digest(ref.digest).is_file()}
    if not required <= present:
        return "contract_references_unavailable"
    return "frozen_contracts_before_first_implementation"


def prepare_context(project, stage, key: str, session: dict, *, continuing: bool) -> tuple:
    """Return session and telemetry; missing prerequisites just preserve the session."""
    policy = read_policy(project)
    reason = ("persistent_policy" if policy["name"] == "persistent" else
              _boundary_reason(project, stage, session, continuing=continuing))
    rotated = reason == "frozen_contracts_before_first_implementation"
    if rotated:
        reset_session(project, stage, key, reason=reason,
                      automatic_boundary="first_driver_implementation")
        session = read_session(project, key)
    return session, {"name": policy["name"], "version": policy["version"],
                     "decision": "rotate" if rotated else "preserve", "reason": reason}
