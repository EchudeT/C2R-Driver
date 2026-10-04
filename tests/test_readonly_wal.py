"""Live read-only consumers must see committed WAL state without gaining write access."""

import sqlite3
from unittest.mock import patch

import pytest

from driver_port_factory.acquisition.contracts import AcquisitionStage
from driver_port_factory.composition import initialize_project, open_project
from driver_port_factory.core.events import RunEvent
from driver_port_factory.core.models import StageStatus
from driver_port_factory.knowledge.bootstrap import KnowledgeBootstrapper
from driver_port_factory.knowledge.index import KnowledgeIndex
from driver_port_factory.target_study.contracts import TargetStudyStage
from driver_port_factory.target_study.service import TargetStudyService
from tests.knowledge_support import prepare_project
from tests.target_support import write_probe_fixture


def test_live_readonly_queries_and_target_acceptance_see_uncheckpointed_commits(tmp_path):
    readers = []

    def initialize(root, config):
        project = initialize_project(root, config)
        reader = sqlite3.connect(project.database_path)
        readers.append(reader)
        assert reader.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
        reader.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        reader.execute("BEGIN")
        reader.execute("SELECT count(*) FROM stages").fetchone()
        return project  # Hold this snapshot so later commits cannot be checkpointed away.

    try:
        with patch("tests.knowledge_support.initialize_project", side_effect=initialize):
            project, _ = prepare_project(tmp_path)
        KnowledgeBootstrapper().build_infrastructure(project)
        uri = project.database_path.as_uri()
        with sqlite3.connect(uri + "?mode=ro&immutable=1", uri=True) as stale:
            assert (
                stale.execute("SELECT status FROM stages WHERE name='evidence_closure'").fetchone()[
                    0
                ]
                == "PENDING"
            )

        readonly = open_project(project.root, read_only=True, verify_artifacts=False)
        assert readonly.stage(AcquisitionStage.EVIDENCE_CLOSURE).status is StageStatus.PASS
        assert KnowledgeIndex.for_project(readonly).status()["status"] == "READY"
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            readonly.record_event(RunEvent.TASK_REUSE, {"must_not_write": True})

        project.start(TargetStudyStage.STUDY)
        report = project.root / "study.md"
        report.write_text("Synthetic WAL test; not a driver validation.\n")
        write_probe_fixture(project, report)
        # Exercises the nested read-only project in the real stage bundle validator.
        TargetStudyService().accept(project, report)
        assert readonly.stage(TargetStudyStage.STUDY).status is StageStatus.PASS
        open_project(project.root, read_only=True).verify_integrity()
    finally:
        for reader in readers:
            reader.close()
