from __future__ import annotations

import atexit
import json
import subprocess
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from driver_port_factory.acquisition.closure import EvidenceClosureFinalizer
from driver_port_factory.acquisition.contracts import AcquisitionArtifact, AcquisitionStage
from driver_port_factory.acquisition.facets import (
    SOURCE_DRIVER_ENTRY,
    EvidenceFacet,
    EvidenceLane,
    FacetDisposition,
    GapReason,
    HardwareFacet,
    LocatorKind,
    MaterialRedistribution,
    QemuFacet,
    TargetFacet,
    TestFacet,
    ToolingFacet,
)
from driver_port_factory.acquisition.proposal import EvidenceProposalImporter, ProposalImport
from driver_port_factory.acquisition.repository_role import RepositoryRole
from driver_port_factory.acquisition.revision_proposal import RevisionProposalImporter
from driver_port_factory.acquisition.revision_selection import RevisionSelector
from driver_port_factory.codex.contracts import CodexArtifact
from driver_port_factory.core.models import GeneratedArtifact
from driver_port_factory.core.project import Project
from driver_port_factory.intake.contracts import IntakeArtifact, IntakeStage


def git(*arguments: str, cwd: Path) -> str:
    return subprocess.run(
        ["git", *arguments], cwd=cwd, check=True, text=True, capture_output=True
    ).stdout.strip()


def repository(root: Path, name: str, files: dict[str, str]) -> Path:
    path = root / name
    path.mkdir()
    git("init", "-b", "main", cwd=path)
    git("config", "user.name", "DPF Test", cwd=path)
    git("config", "user.email", "dpf-test@example.invalid", cwd=path)
    for relative, content in files.items():
        target = path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    git("add", ".", cwd=path)
    git("commit", "-m", "fixture", cwd=path)
    return path


def select_revisions(
    project: Project,
    source: Path,
    target: Path,
    qemu: Path,
    *,
    requested_refs: dict[RepositoryRole, str] | None = None,
) -> None:
    project.start(AcquisitionStage.REVISION_SELECTION)
    proposal = {"repositories": [
        {
            "role": role.value,
            "url": str(path.resolve()),
            "ref": (requested_refs or {}).get(role, git("rev-parse", "HEAD", cwd=path)),
        }
        for role, path in (
            (RepositoryRole.SOURCE, source),
            (RepositoryRole.TARGET, target),
            (RepositoryRole.QEMU, qemu),
        )
    ]}
    job = project.record_artifact(
        AcquisitionStage.REVISION_SELECTION,
        GeneratedArtifact(CodexArtifact.JOB_RESULT, json.dumps(proposal).encode(), "test:revision-selection"),
    )
    assert job.ordinal is not None
    occurrence = RevisionProposalImporter().import_job_result(
        project, job_digest=job.digest, job_ordinal=job.ordinal,
    )
    RevisionSelector().select(project, proposal=occurrence)


class _CompatibilityEvidenceHandler(BaseHTTPRequestHandler):
    content = b""

    def do_GET(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(self.content)))
        self.end_headers()
        self.wfile.write(self.content)

    def log_message(self, format: str, *args: object) -> None:
        return


@contextmanager
def compatibility_evidence_server(content: bytes) -> Iterator[str]:
    handler = type(
        "CompatibilityEvidenceHandler",
        (_CompatibilityEvidenceHandler,),
        {"content": content},
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/compatibility.txt"
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()


def close_evidence(
    project: Project,
    controlled: dict[EvidenceFacet, tuple[RepositoryRole, str]] | None = None,
) -> None:
    imported = import_evidence_proposal(project, evidence_proposal(project, controlled))
    EvidenceClosureFinalizer().finalize(project, proposal=imported.occurrence)


def evidence_proposal(
    project: Project,
    controlled: dict[EvidenceFacet, tuple[RepositoryRole, str]] | None = None,
) -> dict[str, object]:
    controlled = controlled or {}
    envelope = project.load_json_artifact(
        IntakeStage.ENVELOPE_FREEZE, IntakeArtifact.MIGRATION_ENVELOPE
    )
    bindings = {
        SOURCE_DRIVER_ENTRY: (
            RepositoryRole.SOURCE,
            envelope["source_driver_entry_or_repository_hint"],
        ),
        **controlled,
    }
    minimum = {
        SOURCE_DRIVER_ENTRY,
        EvidenceFacet(EvidenceLane.TARGET, TargetFacet.DRIVER_FRAMEWORK),
        EvidenceFacet(EvidenceLane.QEMU, QemuFacet.DEVICE_MODEL),
        EvidenceFacet(EvidenceLane.HARDWARE, HardwareFacet.DEVICE_MANUAL),
        EvidenceFacet(EvidenceLane.TEST, TestFacet.SOURCE_TESTS),
        EvidenceFacet(EvidenceLane.TOOLING, ToolingFacet.TOOLCHAIN_DOCUMENTATION),
        *controlled,
    }
    facets = [
        _facet_proposal(facet, bindings.get(facet))
        for facet in sorted(minimum, key=lambda item: item.sort_key)
    ]
    return {
        "schema_version": 1,
        "migration_envelope_sha256": project.artifact(
            IntakeStage.ENVELOPE_FREEZE, IntakeArtifact.MIGRATION_ENVELOPE
        ).digest,
        "repository_manifest_sha256": project.artifact(
            AcquisitionStage.REPOSITORY_ACQUISITION,
            AcquisitionArtifact.REPOSITORY_MANIFEST,
        ).digest,
        "facets": facets,
    }


def import_evidence_proposal(project: Project, proposal: dict[str, object]) -> ProposalImport:
    project.start(AcquisitionStage.EVIDENCE_CLOSURE)
    data = json.dumps(proposal, sort_keys=True).encode()
    job_ref = project.record_artifact(
        AcquisitionStage.EVIDENCE_CLOSURE,
        GeneratedArtifact(CodexArtifact.JOB_RESULT, data, "test:codex-evidence-proposal"),
    )
    assert job_ref.ordinal is not None
    return EvidenceProposalImporter().import_job_result(
        project,
        job_digest=job_ref.digest,
        job_ordinal=job_ref.ordinal,
    )


def _facet_proposal(
    facet: EvidenceFacet,
    controlled: tuple[RepositoryRole, str] | None,
) -> dict[str, object]:
    policy = {
        "license": "review-required",
        "redistribution": MaterialRedistribution.UNKNOWN.value,
        "original": True,
    }
    if controlled is not None:
        role, path = controlled
        return {
            **facet.to_dict(),
            "disposition": FacetDisposition.CONTROLLED.value,
            "rationale": "fixture provides this controlled facet",
            "locators": [
                {
                    "kind": LocatorKind.GIT_BLOB.value,
                    "repository": role.value,
                    "path": path,
                    **policy,
                }
            ],
        }
    role = {
        EvidenceLane.TARGET: RepositoryRole.TARGET,
        EvidenceLane.QEMU: RepositoryRole.QEMU,
    }.get(facet.lane, RepositoryRole.SOURCE)
    if facet.lane is EvidenceLane.HARDWARE:
        locator = {
            "kind": LocatorKind.EXTERNAL_REFERENCE.value,
            "source_url": unavailable_evidence_url(),
            "max_bytes": 4096,
        }
    else:
        locator = {
            "kind": LocatorKind.GIT_BLOB.value,
            "repository": role.value,
            "path": f".missing-evidence/{facet.lane.value}/{facet.name}",
            **policy,
        }
    return {
        **facet.to_dict(),
        "disposition": FacetDisposition.EXPLICIT_GAP.value,
        "rationale": "fixture intentionally exercises audited gap accounting",
        "locators": [locator],
        "gap": {
            "reason": GapReason.NOT_FOUND.value,
            "impact": "this fixture does not assert behavior requiring the absent evidence",
            "repair_trigger": "add an authoritative controlled input before dependent claims",
        },
    }


class _UnavailableEvidenceHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        self.send_error(404)

    def log_message(self, format: str, *args: object) -> None:
        return


_UNAVAILABLE_SERVER: ThreadingHTTPServer | None = None
_UNAVAILABLE_THREAD: threading.Thread | None = None


def unavailable_evidence_url() -> str:
    global _UNAVAILABLE_SERVER, _UNAVAILABLE_THREAD
    if _UNAVAILABLE_SERVER is None:
        _UNAVAILABLE_SERVER = ThreadingHTTPServer(("127.0.0.1", 0), _UnavailableEvidenceHandler)
        _UNAVAILABLE_THREAD = threading.Thread(
            target=_UNAVAILABLE_SERVER.serve_forever,
            daemon=True,
        )
        _UNAVAILABLE_THREAD.start()
    return f"http://127.0.0.1:{_UNAVAILABLE_SERVER.server_port}/not-found"


def _stop_unavailable_evidence_server() -> None:
    if _UNAVAILABLE_SERVER is not None:
        _UNAVAILABLE_SERVER.shutdown()
        _UNAVAILABLE_SERVER.server_close()


atexit.register(_stop_unavailable_evidence_server)
