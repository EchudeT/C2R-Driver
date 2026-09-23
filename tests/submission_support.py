"""Helpers for exercising the file-backed Codex submission protocol."""

from pathlib import Path

from driver_port_factory.codex.submission import write_submission


def submit(project, job, path: Path, *, kind: str, decision: str,
           operation: str | None = None, repair_stage: str | None = None) -> None:
    write_submission(
        project,
        job.stage,
        job_id=job.job_id,
        file_path=str(path),
        kind=kind,
        decision=decision,
        operation=operation,
        repair_stage=repair_stage,
    )
