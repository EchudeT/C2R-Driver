from pathlib import Path
from unittest.mock import patch

import pytest

from driver_port_factory.codex.gateway import CodexResult
from driver_port_factory.diagnostic import run
from tests.test_workflow_alignment import ready_implementation


def test_diagnostic_keeps_acceptance_ledger_unchanged(tmp_path):
    project = ready_implementation(tmp_path)
    review = tmp_path / "review.md"
    review.write_text("# Missing adapter assertions\nDPF_REVIEW: REWORK\n")
    before = project.database_path.read_bytes()
    jobs = []

    class Gateway:
        def __init__(self, on_event):
            self.on_event = on_event

        def run(self, job):
            jobs.append(job)
            self.on_event({"type": "thread.started", "thread_id": "original-worker"})
            return CodexResult(job.job_id, "DIAGNOSTIC ONLY", "original-worker")

    with patch("driver_port_factory.diagnostic.stage_session", return_value=("key", {
        "thread_id": "original-worker", "documents": {},
    })), patch("driver_port_factory.diagnostic.CodexExecGateway", Gateway):
        attempt = run(project.root, review, Path(project.config.skill_root), "gpt-5.6-sol")
    assert project.database_path.read_bytes() == before
    assert jobs[0].thread_id == "original-worker"
    assert "Keep the current driver implementation unchanged" in jobs[0].prompt
    assert '"acceptance": "NOT_PASSED"' in (attempt / "status.json").read_text()
    assert (attempt / "review.md").read_text() == review.read_text()
    assert (attempt / "events.jsonl").is_file()


def test_diagnostic_rejects_a_pass_report(tmp_path):
    project = ready_implementation(tmp_path)
    review = tmp_path / "review.md"
    review.write_text("DPF_REVIEW: PASS\n")
    with pytest.raises(ValueError, match="actual REWORK"):
        run(project.root, review, Path(project.config.skill_root), "gpt-5.6-sol")
