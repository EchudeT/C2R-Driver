"""One-time, ledgered upgrade of an idle pre-analysis run; never runs old workflows."""
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

from ..core.ledger import append_event, verify_event_chain
from ..core.models import ProjectConfig, WorkflowError
from ..core.events import RunEvent, StageEvent
from .runtime import controller_run


def upgrade(root):
    from ..composition import workflow_for, open_project
    from ..knowledge.bootstrap import KnowledgeBootstrapper
    from ..knowledge.contracts import KnowledgeStage
    from ..source_analysis.contracts import SourceAnalysisStage
    from ..target_study.reuse import remember, restore
    from ..migration.handoff import MigrationHandoff

    root = Path(root).resolve()
    control = root / ".dpf"
    config = ProjectConfig.from_dict(json.loads((control / "project.json").read_text()))
    workflow = workflow_for(config)
    database = control / "run.sqlite3"
    with controller_run(SimpleNamespace(control=control)):
        with sqlite3.connect(database) as db:
            db.row_factory = sqlite3.Row
            if not verify_event_chain(db):
                raise WorkflowError("cannot upgrade a damaged event ledger")
            rows = db.execute("SELECT * FROM stages ORDER BY position").fetchall()
            retired = [r for r in rows if r["name"] == "structured_c_analysis"]
            if not retired:
                raise WorkflowError("run already uses the current protocol")
            boundary = next(r["position"] for r in rows if r["name"] == "source_closure")
            if any(r["status"] not in {"PENDING", "READY"} for r in rows if r["position"] > boundary):
                raise WorkflowError("upgrade requires a run stopped before static analysis")
            if next(r for r in rows if r["name"] == "source_closure")["status"] == "PASS":
                raise WorkflowError("source inputs already finalized; explicit artifact import is required")
            if db.execute("SELECT 1 FROM stage_artifacts WHERE stage_name='structured_c_analysis'").fetchone():
                raise WorkflowError("retired checkpoint has artifacts; explicit import is required")
            backup = control / "pre-protocol-upgrade.sqlite3"
            if backup.exists():
                raise WorkflowError("upgrade recovery snapshot already exists; inspect before retry")
            with sqlite3.connect(backup) as destination:
                db.backup(destination)
            # No state/receipt is marked PASS or discarded. Only unused checkpoint
            # definition is removed; historical retry events remain immutable.
            db.execute("DELETE FROM stages WHERE name='structured_c_analysis'")
            for position, spec in enumerate(workflow.stages):
                db.execute("""UPDATE stages SET position=?,description=?,owner=?,dependencies=?,
                    required_outputs=?,auxiliary_outputs=?,allowed_roles=?,accept_failed_dependencies=?
                    WHERE name=?""", (position, spec.description, spec.owner.value,
                    json.dumps([d.value for d in spec.dependencies]),
                    json.dumps([{"kind": o.value, "cardinality": o.cardinality.value} for o in spec.required_outputs]),
                    json.dumps([o.value for o in spec.auxiliary_outputs]),
                    json.dumps([r.value for r in spec.allowed_roles]), int(spec.accept_failed_dependencies), spec.name.value))
            append_event(db, RunEvent.PROTOCOL_UPGRADED,
                         {"retired_stages": ["structured_c_analysis"], "protocol": "skill-task-v1",
                          "recovery_snapshot": str(backup)})
            # Retire the obsolete report submission, not its work files or conversation.
            # The revised source task must receive the new protocol before acceptance.
            from ..core.artifact_persistence import next_ordinal
            from ..core.models import ArtifactDirection
            boundaries = {d.value: next_ordinal(db, "source_closure", d) for d in ArtifactDirection}
            db.execute("UPDATE stages SET status='READY',started_at=NULL,completed_at=NULL,message=NULL WHERE name='source_closure'")
            append_event(db, StageEvent.RETRIED, {
                "stage": "source_closure", "trigger": "source_closure", "status": "READY",
                "actor_role": config.actor_role.value, "artifact_boundaries": boundaries,
                "reason": "Protocol upgrade: reuse existing compile_commands and source work; complete source analysis in one task.",
            })
        project = open_project(root)
        # Rebind the generated interface through normal validated commits. Restore
        # target study only after verifying its unchanged non-source inputs.
        remember(project)
        project.start(SourceAnalysisStage.SOURCE_CLOSURE)
        project.retry_from(KnowledgeStage.KNOWLEDGE_BASE,
            trigger=SourceAnalysisStage.SOURCE_CLOSURE,
            reason="Protocol upgrade: regenerate the read-only KB interface; preserve substantive work")
        KnowledgeBootstrapper().build_infrastructure(project)
        if not restore(project):
            raise WorkflowError("target study inputs changed during upgrade; inspect before resuming")
        MigrationHandoff().create(project)
        project.verify_integrity()
    return {"protocol": "skill-task-v1", "source_stage": project.stage(workflow.parse_stage("source_closure")).status.value}
