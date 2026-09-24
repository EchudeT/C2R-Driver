"""Real local Git acquisition and controller restart; no network or model billing."""

import json
from unittest.mock import patch

import pytest

from driver_port_factory.acquisition.contracts import (
    AcquisitionArtifact as A,
    AcquisitionStage as S,
)
from driver_port_factory.acquisition.git_execution import RepositoryGit, RepositoryFetchError
from driver_port_factory.acquisition.commands import RepositoryCommandKind as K
from driver_port_factory.acquisition.repository_role import RepositoryRole
from driver_port_factory.codex.gateway import CodexResult
from driver_port_factory.composition import open_project
from tests.acquisition_support import git
from tests.repository_support import ready_project
from tests.workflow_support import runner
from tests.submission_support import submit


@pytest.mark.parametrize("interrupt", [False, True])
def test_single_download_selection_to_frozen_baselines(tmp_path, interrupt):
    from dataclasses import replace
    from tests.repository_support import project_config

    config = replace(project_config(), skill_root=str(tmp_path / "skills"),
                     baseline_repositories=(str(tmp_path / "upstream-cache"),))
    with (
        patch("tests.repository_support.select_revisions"),
        patch("tests.repository_support.project_config", return_value=config),
    ):
        project, source, target, qemu = ready_project(tmp_path)
    # Exercise the actual annotated-tag SHA that broke the old split workflow.
    git("tag", "-a", "v2.0.0", "-m", "annotated", cwd=source)
    cache = tmp_path / "upstream-cache"
    git("clone", str(source), str(cache), cwd=tmp_path)
    (cache / "drivers/example.c").write_text("uncommitted cache edit must not be imported\n")
    selection = {
        "repositories": [
            {
                "role": role.value,
                "url": str(path),
                "ref": git("rev-parse", "v2.0.0", cwd=path) if path == source else "v1.0.0",
            }
            for role, path in zip(RepositoryRole, (source, target, qemu), strict=True)
        ]
    }
    skill = tmp_path / "skills/open-kernel-driver-port"
    (skill / "references").mkdir(parents=True)
    (skill / "SKILL.md").write_text("Use originals and retain downloaded evidence.\n")
    (skill / "references/acquisition.md").write_text("Fetch once and freeze the actual commit.\n")
    port = runner(project)
    fetches = []
    original = RepositoryGit.run
    failed = False

    def execute(self, args, **kwargs):
        nonlocal failed
        if kwargs["operation"] is K.BASELINE_FETCH:
            role = kwargs["role"]
            if interrupt and role is RepositoryRole.TARGET and not failed:
                failed = True
                raise RepositoryFetchError("synthetic interrupted network; retain source download")
            fetches.append(role)
        return original(self, args, **kwargs)

    def worker(job):
        assert job.stage is S.REPOSITORY_ACQUISITION
        proposal = job.execution_root / "repository-selection.json"
        proposal.write_text(json.dumps(selection))
        submit(project, job, proposal, kind="proposal", decision="submit")
        return CodexResult(job.job_id, "", "worker")

    with (
        patch("driver_port_factory.codex.cli.CodexExecGateway.run", side_effect=worker) as model,
        patch.object(RepositoryGit, "run", execute),
    ):
        if interrupt:
            with pytest.raises(RepositoryFetchError):
                port._run_project(project)
            project = open_project(project.root)
        port._repositories(project)
    assert model.call_count == 1
    assert fetches == list(RepositoryRole)
    assert project.stage(S.REPOSITORY_ACQUISITION).status.value == "PASS"
    assert len(project.stages()) == 18
    assert "revision_selection" not in project.workflow.stage_values
    frozen = project.load_json_artifact(S.REPOSITORY_ACQUISITION, A.REVISION_MANIFEST)
    assert frozen["source"]["revision"] == git("rev-parse", "v2.0.0^{commit}", cwd=source)
    manifest = project.load_json_artifact(S.REPOSITORY_ACQUISITION, A.REPOSITORY_MANIFEST)
    assert any(row["operation"] == K.BASELINE_CACHE_IMPORT.value and "fetch" in row["result"]["argv"]
               for row in manifest["commands"])
    checkout = next(row for row in manifest["checkouts"] if row["role"] == "source")
    assert (project.root / checkout["checkout_path"] / "drivers/example.c").read_text() == "/* source driver */\n"
    assert "uncommitted" in (cache / "drivers/example.c").read_text()
    assert not (project.control / "revision-probes").exists()
    open_project(project.root).verify_integrity()


def test_selection_can_change_before_acquisition_is_frozen(tmp_path):
    from driver_port_factory.acquisition.repository import RepositoryAcquirer
    from driver_port_factory.acquisition.revision_proposal import RevisionProposalImporter
    from driver_port_factory.codex.contracts import CodexArtifact
    from driver_port_factory.core.models import GeneratedArtifact, WorkflowError

    project, source, target, qemu = ready_project(tmp_path)
    from driver_port_factory.acquisition.git_execution import RepositorySelectionError
    from driver_port_factory.acquisition.repository_storage import BareRepositoryStore
    from types import SimpleNamespace
    store = BareRepositoryStore(project.root, project.control,
                                RepositoryGit(project.root, project.control))
    missing = SimpleNamespace(role=RepositoryRole.SOURCE, url=str(source),
                              requested_ref="v-does-not-exist")
    with pytest.raises(RepositorySelectionError):
        store.fetch(store.prepare(missing), missing)
    missing.requested_ref = "1" * 40
    with pytest.raises(RepositorySelectionError):
        store.fetch(store.prepare(missing), missing)
    # Ambiguous transport failure can be explicitly returned to the worker,
    # without discarding the existing selection/download or restarting a phase.
    from driver_port_factory.core.checker_decision import resume_recovery, pending_decision, clear_pending
    RepositoryAcquirer._record_attempt(project, RepositoryFetchError("fixture unavailable origin"))
    resume_recovery(project, S.REPOSITORY_ACQUISITION, "check whether selected origin is incorrect")
    assert pending_decision(open_project(project.root), S.REPOSITORY_ACQUISITION) is not None
    clear_pending(project, S.REPOSITORY_ACQUISITION)
    with patch("driver_port_factory.acquisition.repository.SourceIdentityVerifier.verify",
               side_effect=WorkflowError("selected version does not support confirmed scope")):
        with pytest.raises(WorkflowError):
            RepositoryAcquirer().acquire(project)
    old_locks = {p: p.read_bytes() for p in (project.control / "manifests/repository-locks").glob("*.json")}
    for path in (source, target):
        (path / "version.txt").write_text("corrected version\n")
        git("add", "version.txt", cwd=path)
        git("commit", "-m", "correct selection", cwd=path)
    selection = {"repositories": [
        {"role": role.value, "url": str(path), "ref": git("rev-parse", "HEAD", cwd=path)}
        for role, path in zip(RepositoryRole, (source, target, qemu), strict=True)]}
    job = project.record_artifact(S.REPOSITORY_ACQUISITION, GeneratedArtifact(
        CodexArtifact.JOB_RESULT, json.dumps(selection).encode(), "fixture:corrected-selection"))
    imported = RevisionProposalImporter().import_job_result(
        project, job_digest=job.digest, job_ordinal=job.ordinal)
    acquisition = RepositoryAcquirer().acquire(project, proposal=imported)
    assert acquisition.target_worktree.base_commit == git("rev-parse", "HEAD", cwd=target)
    assert all(path.read_bytes() == content for path, content in old_locks.items())
    open_project(project.root).verify_integrity()


def test_official_document_is_downloaded_once_through_evidence_closure(tmp_path):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from threading import Thread
    from driver_port_factory.acquisition.repository import RepositoryAcquirer
    from driver_port_factory.acquisition.document_binding import ExternalDocumentBinder
    from driver_port_factory.acquisition.closure import EvidenceClosureFinalizer
    from tests.acquisition_support import evidence_proposal

    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append(self.path)
            body = b"Synthetic manufacturer original device manual."
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        project, *_ = ready_project(tmp_path)
        RepositoryAcquirer().acquire(project)
        project.start(S.EVIDENCE_CLOSURE)
        publisher = f"http://127.0.0.1:{server.server_port}"
        choice = {
            "url": publisher + "/manual.txt",
            "publisher_url": publisher,
            "basis": "Synthetic fixture manufacturer original, not a real hardware claim.",
        }
        locator = ExternalDocumentBinder(project).bind(choice)
        # Restart between URL selection and finalization; no second network read.
        project = open_project(project.root)
        assert ExternalDocumentBinder(project).bind(choice) == locator
        selection = evidence_proposal(project)
        selection["facets"] = [
            item
            if item["lane"] != "hardware"
            else {
                "lane": "hardware",
                "facet": item["facet"],
                "disposition": "CONTROLLED",
                "rationale": "Synthetic original publisher",
                "locators": [locator.to_dict()],
            }
            for item in selection["facets"]
        ]
        # Import helper normally starts the ready stage; this stage already started.
        from driver_port_factory.codex.contracts import CodexArtifact
        from driver_port_factory.core.models import GeneratedArtifact
        from driver_port_factory.acquisition.proposal import EvidenceProposalImporter

        job = project.record_artifact(
            S.EVIDENCE_CLOSURE,
            GeneratedArtifact(CodexArtifact.JOB_RESULT, json.dumps(selection).encode(), "fixture"),
        )
        proposal = EvidenceProposalImporter().import_job_result(
            project, job_digest=job.digest, job_ordinal=job.ordinal
        )
        EvidenceClosureFinalizer().finalize(project, proposal=proposal.occurrence)
        assert requests == ["/manual.txt"]
        open_project(project.root).verify_integrity()
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
