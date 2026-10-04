"""Early local discovery and evidence-backed gaps, without model or external network calls."""

import json
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import pytest

from driver_port_factory.acquisition.closure import EvidenceClosureFinalizer
from driver_port_factory.acquisition.contracts import (
    AcquisitionArtifact as A,
    AcquisitionStage as S,
)
from driver_port_factory.acquisition.facets import parse_facet
from driver_port_factory.acquisition.proposal import EvidenceDiscoveryProposal
from driver_port_factory.acquisition.repository import RepositoryAcquirer
from driver_port_factory.acquisition.repository_role import RepositoryRole
from driver_port_factory.composition import open_project
from driver_port_factory.core.models import WorkflowError
from driver_port_factory.knowledge.acquisition_context import context
from driver_port_factory.knowledge.shared import import_git
from driver_port_factory.knowledge.shared_binding import freeze, project_binding
from driver_port_factory.knowledge.shared_cli import query_project
from driver_port_factory.knowledge.shared_store import Library
from tests.acquisition_support import evidence_proposal, import_evidence_proposal
from tests.repository_support import ready_project


def ready(tmp_path):
    project, source, target, qemu = ready_project(tmp_path)
    RepositoryAcquirer().acquire(project)
    return project, source, target, qemu


def seed(tmp_path, source):
    library = Library(tmp_path / "library")
    imported = import_git(
        library,
        source,
        "HEAD",
        ["drivers/example.c"],
        domain="source",
        platform="example-source",
        source_url=str(source),
        license_note="synthetic fixture",
    )
    return library, imported


def local_proposal(project):
    proposal = evidence_proposal(
        project,
        {
            parse_facet("qemu", "device_model"): (RepositoryRole.QEMU, "hw/example.c"),
        },
    )
    for facet in proposal["facets"]:
        if facet["lane"] == "hardware":
            facet["locators"] = []
            facet["gap"] = {
                "impact": "Synthetic model evidence does not establish physical hardware behavior",
                "repair_trigger": "Supply primary hardware evidence before physical hardware claims",
                "basis": [{"lane": "qemu", "facet": "device_model"}],
            }
    return proposal


def test_early_shared_context_is_pinned_across_restart_head_and_environment_changes(
    tmp_path, monkeypatch
):
    project, source, _, _ = ready(tmp_path)
    library, imported = seed(tmp_path, source)
    monkeypatch.setenv("DPF_SHARED_KB", str(library.root))
    packet = context(project)
    assert project.stage(S.EVIDENCE_CLOSURE).status.value == "READY"
    assert packet["shared_library"]["snapshot"] == imported["snapshot"]
    assert any(row["packet"]["results"] for row in packet["initial_matches"])
    assert all(Path(row["path"]).is_dir() for row in packet["local_repositories"])
    with library.writing():
        library.commit(retire=(imported["entries"][0], "later revision"))
    monkeypatch.setenv("DPF_SHARED_KB", str(tmp_path / "unrelated-missing-library"))
    reopened = open_project(project.root, read_only=True, verify_artifacts=False)
    assert query_project(reopened, "source driver")["snapshot"] == imported["snapshot"]
    assert query_project(reopened, "source driver")["results"]
    assert context(project)["shared_library"] == packet["shared_library"]
    assert query_project(reopened, "source driver", revision="different")["results"] == []
    assert query_project(reopened, "source driver", repository="source")["results"]
    with pytest.raises(WorkflowError, match="conflict"):
        query_project(reopened, "source driver", repository="source", revision="different")
    import subprocess
    import sys

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "driver_port_factory.cli",
            "knowledge",
            "shared-search",
            str(project.root),
            "--query",
            "source driver",
            "--repository",
            "source",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(result.stdout)["results"]


def test_no_library_is_frozen_without_blocking_local_selection(tmp_path, monkeypatch):
    monkeypatch.delenv("DPF_SHARED_KB", raising=False)
    project, _, _, _ = ready(tmp_path)
    assert context(project)["shared_library"] is None
    monkeypatch.setenv("DPF_SHARED_KB", str(tmp_path / "missing"))
    assert freeze(project) is None
    assert query_project(project, "driver")["status"] == "NOT_CONFIGURED"


def test_knowledge_build_inherits_early_binding(tmp_path, monkeypatch):
    from driver_port_factory.knowledge.bootstrap import KnowledgeBootstrapper
    from driver_port_factory.knowledge.contracts import KnowledgeArtifact, KnowledgeStage
    from tests.knowledge_support import prepare_project

    project, checkouts = prepare_project(tmp_path)
    library, imported = seed(tmp_path, project.root / checkouts["source"].checkout_path)
    monkeypatch.setenv("DPF_SHARED_KB", str(library.root))
    context(project)
    monkeypatch.delenv("DPF_SHARED_KB")
    KnowledgeBootstrapper().build_infrastructure(project)
    contract = project.load_json_artifact(
        KnowledgeStage.KNOWLEDGE_BASE, KnowledgeArtifact.QUERY_CONTRACT
    )
    assert contract["shared_library"]["snapshot"] == imported["snapshot"]
    assert project_binding(project) == contract["shared_library"]


def test_supported_gap_finishes_with_no_http_and_remains_a_gap(tmp_path):
    project, _, _, _ = ready(tmp_path)
    proposal = local_proposal(project)
    with patch(
        "driver_port_factory.acquisition.http_transport.HttpTransport.download",
        side_effect=AssertionError("No external material is needed"),
    ):
        imported = import_evidence_proposal(project, proposal)
        EvidenceClosureFinalizer().finalize(project, proposal=imported.occurrence)
    gap = next(
        g
        for g in project.load_json_artifact(S.EVIDENCE_CLOSURE, A.EVIDENCE_GAP_REGISTER)["gaps"]
        if g["lane"] == "hardware"
    )
    assert gap["basis"] == [{"lane": "qemu", "facet": "device_model"}]
    assert gap["retrieval_attempt_ids"] == []
    materials = project.artifacts.read(project.artifact(S.EVIDENCE_CLOSURE, A.MATERIALS_MANIFEST))
    assert not any(json.loads(line)["domain"] == "hardware" for line in materials.splitlines())
    open_project(project.root).verify_integrity()


def test_gap_cannot_rely_on_a_selected_original_that_failed_collection(tmp_path):
    project, _, _, _ = ready(tmp_path)
    proposal = local_proposal(project)
    model = next(f for f in proposal["facets"] if f["lane"] == "qemu")
    model["locators"][0]["path"] = "missing-model.c"
    imported = import_evidence_proposal(project, proposal)
    with pytest.raises(WorkflowError, match="retrieved no material"):
        EvidenceClosureFinalizer().finalize(project, proposal=imported.occurrence)


def test_legacy_project_keeps_accepted_binding_without_early_event(tmp_path, monkeypatch):
    from driver_port_factory.knowledge.bootstrap import KnowledgeBootstrapper
    from tests.knowledge_support import prepare_project

    project, checkouts = prepare_project(tmp_path)
    library, imported = seed(tmp_path, project.root / checkouts["source"].checkout_path)
    bound = {"root": str(library.root), "snapshot": imported["snapshot"], "use": "legacy fixture"}
    with patch("driver_port_factory.knowledge.shared_binding.freeze", return_value=bound):
        KnowledgeBootstrapper().build_infrastructure(project)
    monkeypatch.setenv("DPF_SHARED_KB", str(tmp_path / "unrelated"))
    assert freeze(project) == bound
    assert (
        query_project(open_project(project.root), "source driver")["snapshot"]
        == imported["snapshot"]
    )


@pytest.mark.parametrize(
    "basis",
    [[], [{"lane": "qemu", "facet": "unknown"}], [{"lane": "hardware", "facet": "device_manual"}]],
)
def test_empty_unknown_or_gap_basis_cannot_replace_collected_evidence(tmp_path, basis):
    project, _, _, _ = ready(tmp_path)
    proposal = local_proposal(project)
    hardware = next(f for f in proposal["facets"] if f["lane"] == "hardware")
    hardware["gap"]["basis"] = basis
    with pytest.raises(WorkflowError, match="basis"):
        EvidenceDiscoveryProposal.from_dict(proposal)


def test_real_evidence_worker_receives_reuse_packet_and_submits_local_gap(tmp_path, monkeypatch):
    from driver_port_factory.codex.gateway import CodexResult
    from tests.repository_support import project_config
    from tests.submission_support import submit
    from tests.workflow_support import runner

    skill = tmp_path / "skills/open-kernel-driver-port"
    (skill / "references").mkdir(parents=True)
    for name in ("SKILL.md", "references/acquisition.md", "references/handoff.md"):
        (skill / name).write_text("Synthetic test instruction: select frozen originals.\n")
    with patch(
        "tests.repository_support.project_config",
        return_value=replace(project_config(), skill_root=str(skill.parent)),
    ):
        project, source, _, _ = ready(tmp_path)
    library, _ = seed(tmp_path, source)
    monkeypatch.setenv("DPF_SHARED_KB", str(library.root))
    rows = []
    for row in local_proposal(project)["facets"]:
        selected = {k: row[k] for k in ("lane", "facet", "rationale")}
        selected["repository_paths"] = [
            {"repository": r["repository"], "path": r["path"]} for r in row["locators"]
        ]
        if "gap" in row:
            selected["gap"] = row["gap"]
        rows.append(selected)

    def worker(job):
        packet = json.loads(job.prompt.split("<job>")[1].split("</job>")[0])["reference_material"]
        assert packet["evidence_reuse"]["shared_library"]
        assert len(packet["evidence_reuse"]["local_repositories"]) == 3
        proposal = project.root / "selection.json"
        proposal.write_text(json.dumps({"facets": rows}))
        submit(project, job, proposal, kind="proposal", decision="submit")
        return CodexResult(job.job_id, "", "synthetic-worker")

    with (
        patch("driver_port_factory.codex.cli.CodexExecGateway.run", side_effect=worker),
        patch(
            "driver_port_factory.acquisition.http_transport.HttpTransport.download",
            side_effect=AssertionError("unexpected HTTP"),
        ),
    ):
        runner(project)._evidence(project)
    assert project.stage(S.EVIDENCE_CLOSURE).status.value == "PASS"
