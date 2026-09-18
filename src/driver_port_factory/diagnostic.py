"""Explicitly authorized downstream experiments without changing acceptance gates."""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from pathlib import Path

from .acquisition.repository import load_repository_acquisition
from .codex.gateway import CodexExecGateway, CodexJob
from .codex.policy import CodexExecutionPolicy
from .codex.prompts import SkillPromptComposer
from .codex.sessions import read_session, session_key
from .composition import WORKFLOW_STAGE_CATALOG, open_project
from .migration.contracts import MigrationStage


def run(workspace: Path, review: Path, skill_root: Path, model: str) -> Path:
    project = open_project(workspace, read_only=True)
    review = review.resolve(strict=True)
    review_text = review.read_text()
    if not review_text.rstrip().endswith("DPF_REVIEW: REWORK"):
        raise ValueError("diagnostic continuation requires an actual REWORK report")
    acquisition = load_repository_acquisition(project)
    worktree = (project.root / acquisition.target_worktree.path).resolve()
    stage = MigrationStage.ARTIFACT_PREPARATION
    grant = CodexExecutionPolicy().grant(project, stage)
    session = read_session(project, session_key(project, stage, grant, model, "exec"))
    if not session.get("thread_id"):
        raise ValueError("existing implementation conversation is required")
    attempt = project.control / "diagnostic-runs" / str(uuid.uuid4())
    attempt.mkdir(parents=True)
    (attempt / "review.md").write_text(review_text)
    objective = (
        "USER-AUTHORIZED DIAGNOSTIC CONTINUATION ONLY. Compliance remains REWORK; "
        "this is not acceptance and must not change any controller state or claim full PASS. "
        "Keep the current driver implementation unchanged. Do not repair review findings or "
        "perform any non-functional edits. Reuse existing passing builds/tests. Complete the "
        "artifact-preparation and public-QEMU evidence work as one downstream diagnostic: "
        "discover the evidenced official runner/container/SDK or packaging route, recover "
        "missing host tools through available supported alternatives, prepare an artifact "
        "containing this driver, and actually run the public QEMU evidence ladder with bounded "
        "timeouts and preserved logs. Missing a host binary alone is not a terminal blocker. "
        "Create reproducible build/presence/run scripts under .dpf-output/diagnostic and keep "
        "attempt logs separate; do not overwrite prior evidence. Follow the supplied Skill's "
        "qemu-evidence reference. No blind evaluation or private tests. Report actual commands, "
        "artifact identity, driver inclusion, observations and remaining failures honestly. "
        "If a functional change is needed, report the causal evidence without editing the driver. "
        "Finish with REPORT_PATH to a Markdown diagnostic report in the writable worktree. "
        "Clearly label all results DIAGNOSTIC / COMPLIANCE NOT PASSED."
    )
    rendered = SkillPromptComposer(
        skill_root, WORKFLOW_STAGE_CATALOG, project.workflow.stage_values
    ).render(
        stage=stage,
        actor_role=project.config.actor_role,
        objective=objective,
        context={
            "project_root": str(project.root),
            "target_worktree": str(worktree),
            "failed_review_path": str(attempt / "review.md"),
            "diagnostic_only": True,
            "tool_runtime": {
                "python": sys.executable,
                "workflow_cli": [sys.executable, "-m", "driver_port_factory.cli"],
                "cargo_home": str(worktree / ".dpf-output/cargo-home"),
            },
        },
        known_documents=session.get("documents", {}),
    )
    (attempt / "prompt.txt").write_text(rendered.text)
    (attempt / "status.json").write_text(json.dumps({
        "diagnostic_only": True, "acceptance": "NOT_PASSED", "status": "RUNNING",
        "thread_id": session["thread_id"],
    }))

    def checkpoint(event: dict) -> None:
        with (attempt / "events.jsonl").open("a") as stream:
            stream.write(json.dumps(event) + "\n")

    result = CodexExecGateway(on_event=checkpoint).run(CodexJob(
        stage=stage, actor_role=project.config.actor_role, objective=objective,
        prompt=rendered.text, execution_root=worktree, sandbox=grant.sandbox,
        model=model, thread_id=session["thread_id"],
    ))
    (attempt / "response.md").write_text(result.final_response)
    (attempt / "status.json").write_text(json.dumps({
        "diagnostic_only": True, "acceptance": "NOT_PASSED",
        "status": "ERROR" if result.error else "REPORTED",
        "error": result.error, "thread_id": result.thread_id,
    }))
    if result.error:
        raise RuntimeError(result.error)
    return attempt


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workspace", type=Path)
    parser.add_argument("--review", type=Path, required=True)
    parser.add_argument("--skill-root", type=Path, required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--allow-unreviewed-diagnostic", action="store_true", required=True)
    args = parser.parse_args()
    print(run(args.workspace, args.review, args.skill_root, args.model))
