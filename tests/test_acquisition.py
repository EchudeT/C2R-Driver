from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from driver_port_factory.acquisition.baseline import BaselineRepositoryAcquirer
from driver_port_factory.acquisition.closure import EvidenceClosureFinalizer
from driver_port_factory.acquisition.commands import RepositoryCommandKind
from driver_port_factory.acquisition.contracts import AcquisitionArtifact, AcquisitionStage
from driver_port_factory.acquisition.evidence_validation import validate_evidence_closure_bundle
from driver_port_factory.acquisition.facets import (
    SOURCE_DRIVER_ENTRY,
    EvidenceFacet,
    EvidenceLane,
    FacetDisposition,
    GapReason,
    HardwareFacet,
    LocatorKind,
    MaterialRedistribution,
    SourceFacet,
    TargetFacet,
)
from driver_port_factory.acquisition.job import ArtifactOccurrence
from driver_port_factory.acquisition.locators import parse_locator
from driver_port_factory.acquisition.proposal import EvidenceProposalImporter
from driver_port_factory.acquisition.repository import (
    RepositoryAcquirer,
    load_repository_acquisition,
)
from driver_port_factory.acquisition.repository_manifest import RepositoryAcquisition
from driver_port_factory.acquisition.repository_role import RepositoryRole
from driver_port_factory.acquisition.repository_validation import validate_repository_bundle
from driver_port_factory.acquisition.retrieval import (
    EvidenceRetriever,
    material_identifier,
)
from driver_port_factory.acquisition.retrieval_result import RetrievalFailure, RetrievedMaterial
from driver_port_factory.acquisition.revision_compatibility import (
    CompatibilityClaimKind,
)
from driver_port_factory.acquisition.revision_evidence import RevisionEvidenceRetriever
from driver_port_factory.acquisition.revision_proposal import (
    CompatibilityCitation,
    ProposedRevisionBinding,
    RevisionSelectionProposal,
)
from driver_port_factory.acquisition.revision_resolution import RevisionResolver
from driver_port_factory.acquisition.revision_selection import RevisionSelector
from driver_port_factory.acquisition.verification import AcquisitionVerifier
from driver_port_factory.codex.contracts import CodexArtifact
from driver_port_factory.composition import initialize_project
from driver_port_factory.core.models import (
    ActorRole,
    ArtifactDirection,
    EvaluationMode,
    GeneratedArtifact,
    ProjectConfig,
    StageStatus,
    WorkflowError,
)
from driver_port_factory.core.validation import BundleValidationContext
from driver_port_factory.environment.contracts import EnvironmentStage
from driver_port_factory.intake.contracts import IntakeStage
from driver_port_factory.intake.service import IntakeService
from tests.acquisition_support import (
    close_evidence,
    compatibility_evidence_server,
    evidence_proposal,
    git,
    import_evidence_proposal,
    repository,
    select_revisions,
)


def project_config() -> ProjectConfig:
    return ProjectConfig(
        project_id="acquisition-test",
        source_platform="example-source",
        target_platform="example-target",
        driver_name="example-driver",
        evaluation_mode=EvaluationMode.DEVELOPER_EVIDENCE,
        actor_role=ActorRole.DEVELOPER,
    )


def ready_project(
    root: Path,
    *,
    source_overrides: dict[str, str] | None = None,
    target_overrides: dict[str, str] | None = None,
):
    source = repository(
        root,
        "source",
        source_overrides
        or {
            "drivers/example.c": "/* source driver */\n",
            "docs/original.txt": "original source contract\n",
            "docs/derived.txt": "derived source contract\n",
        },
    )
    target = repository(root, "target", target_overrides or {"README.md": "target\n"})
    qemu = repository(root, "qemu", {"hw/example.c": "/* model */\n"})
    catalog = root / "drivers.json"
    catalog.write_text(
        json.dumps(
            {
                "source_platform": "example-source",
                "drivers": [
                    {
                        "candidate_id": "example-driver",
                        "canonical_name": "example-driver",
                        "source_entry_hint": "drivers/example.c",
                        "device_family": "Example device",
                        "bus_or_transport": "TESTBUS",
                        "aliases": [],
                        "device_scope": ["Example device"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    project = initialize_project(root / "run", project_config())
    IntakeService().analyze(project, raw_request="port example", catalog_paths=(catalog,))
    for path in (source, target, qemu):
        git("tag", "v1.0.0", cwd=path)
    select_revisions(
        project,
        source,
        target,
        qemu,
        requested_refs={role: "v1.0.0" for role in RepositoryRole},
    )
    return project, source, target, qemu


def repository_bundle_context(
    project,
    *,
    repository_manifest: dict[str, object] | None = None,
) -> BundleValidationContext:
    stage = AcquisitionStage.REPOSITORY_ACQUISITION
    artifacts = []
    for ref in project.artifact_refs(stage=stage, direction=ArtifactDirection.OUTPUT):
        data = project.artifacts.read(ref)
        if (
            repository_manifest is not None
            and ref.kind == AcquisitionArtifact.REPOSITORY_MANIFEST.value
        ):
            data = json.dumps(repository_manifest, sort_keys=True).encode("utf-8")
        artifacts.append((ref, data))
    dependencies = tuple(
        (ref, project.artifacts.read(ref))
        for dependency in project.workflow.spec(stage).dependencies
        for ref in project.artifact_refs(
            stage=dependency,
            direction=ArtifactDirection.OUTPUT,
        )
    )
    return BundleValidationContext(project.root, tuple(artifacts), dependencies)


def evidence_bundle_context(
    project,
    *,
    overrides: dict[AcquisitionArtifact, bytes] | None = None,
    excluded_auxiliary_ordinals: frozenset[int] = frozenset(),
) -> BundleValidationContext:
    stage = AcquisitionStage.EVIDENCE_CLOSURE
    required = {item.value for item in project.stage(stage).required_outputs}
    replacements = overrides or {}
    final_artifacts = []
    auxiliary_artifacts = []
    for ref in project.artifact_refs(stage=stage, direction=ArtifactDirection.OUTPUT):
        data = project.artifacts.read(ref)
        try:
            kind = AcquisitionArtifact(ref.kind)
        except ValueError:
            kind = None
        if kind in replacements:
            data = replacements[kind]
        item = (ref, data)
        if ref.kind in required:
            final_artifacts.append(item)
        elif ref.ordinal not in excluded_auxiliary_ordinals:
            auxiliary_artifacts.append(item)
    dependencies = tuple(
        (ref, project.artifacts.read(ref))
        for dependency in project.workflow.spec(stage).dependencies
        for ref in project.artifact_refs(
            stage=dependency,
            direction=ArtifactDirection.OUTPUT,
        )
    )
    return BundleValidationContext(
        project.root,
        tuple(final_artifacts),
        dependencies,
        tuple(auxiliary_artifacts),
    )


def set_external_proposal(
    proposal: dict[str, object],
    *,
    facet: EvidenceFacet,
    source_url: str,
    content: bytes,
    authority: dict[str, object],
    disposition: FacetDisposition = FacetDisposition.CONTROLLED,
) -> None:
    item = next(
        (
            item
            for item in proposal["facets"]
            if (item["lane"], item["facet"]) == (facet.lane.value, facet.name)
        ),
        None,
    )
    if item is None:
        template = next(item for item in proposal["facets"] if item["lane"] == facet.lane.value)
        item = {**template, "facet": facet.name}
        proposal["facets"].append(item)
    item["disposition"] = disposition.value
    item["locators"] = [
        {
            "kind": LocatorKind.EXTERNAL_URL.value,
            "source_url": source_url,
            "revision": "fixture-v1",
            "expected_sha256": hashlib.sha256(content).hexdigest(),
            "max_bytes": 4096,
            "authority": authority,
            "license": "review-required",
            "redistribution": MaterialRedistribution.UNKNOWN.value,
            "original": True,
        }
    ]
    if disposition is FacetDisposition.CONTROLLED:
        item.pop("gap", None)
    else:
        item["gap"] = {
            "reason": GapReason.CONFLICTING_SOURCES.value,
            "impact": "conflicting external retrieval cannot support a controlled claim",
            "repair_trigger": "provide hash-bound independently corroborated content",
        }


class AcquisitionTests(unittest.TestCase):
    def test_remote_full_commit_requires_active_origin_membership_proof(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            origin = repository(root, "remote-origin", {"README": "remote\n"})
            commit = git("rev-parse", "HEAD", cwd=origin)
            project_root = root / "run"
            control_root = project_root / ".dpf"
            control_root.mkdir(parents=True)
            resolver = RevisionResolver(project_root, control_root)
            resolved = resolver.resolve(
                role=RepositoryRole.SOURCE,
                platform="source-platform",
                url=origin.as_uri(),
                requested_ref=commit,
                selection_rule="full commit fixture",
            )
            self.assertEqual(resolved.resolved_commit, commit)
            self.assertIn(
                RepositoryCommandKind.COMMIT_MEMBERSHIP,
                {record.operation for record in resolver.commands},
            )

    def test_repository_manifest_rejects_untyped_command_records(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project, _, _, _ = ready_project(Path(temporary))
            manifest = RepositoryAcquirer().acquire(project).to_dict()
            manifest["commands"] = [{}]
            with self.assertRaisesRegex(WorkflowError, "repository command record"):
                RepositoryAcquisition.from_dict(manifest)

    def test_repository_bundle_rejects_invalid_writable_target_identity(self) -> None:
        mutations = (
            ("missing", lambda value: value.update(path="work/missing-target")),
            ("control-overlap", lambda value: value.update(path=".dpf")),
        )
        for label, mutate in mutations:
            with self.subTest(case=label), tempfile.TemporaryDirectory() as temporary:
                project, _, _, _ = ready_project(Path(temporary))
                RepositoryAcquirer().acquire(project)
                document = project.load_json_artifact(
                    AcquisitionStage.REPOSITORY_ACQUISITION,
                    AcquisitionArtifact.REPOSITORY_MANIFEST,
                )
                mutate(document["target_worktree"])
                with self.assertRaises(WorkflowError):
                    validate_repository_bundle(
                        repository_bundle_context(project, repository_manifest=document)
                    )

    def test_repository_bundle_rejects_target_head_or_branch_drift(self) -> None:
        for drift in ("head", "branch"):
            with self.subTest(drift=drift), tempfile.TemporaryDirectory() as temporary:
                project, _, _, _ = ready_project(Path(temporary))
                acquisition = RepositoryAcquirer().acquire(project)
                target = project.root / acquisition.target_worktree.path
                if drift == "head":
                    (target / "new-file").write_text("migration work\n", encoding="utf-8")
                    git("add", "new-file", cwd=target)
                    git(
                        "-c",
                        "user.name=DPF Test",
                        "-c",
                        "user.email=dpf@example.invalid",
                        "commit",
                        "-m",
                        "advance target",
                        cwd=target,
                    )
                else:
                    git("switch", "-c", "unexpected-target-branch", cwd=target)
                with self.assertRaisesRegex(WorkflowError, "target worktree"):
                    validate_repository_bundle(repository_bundle_context(project))

    def test_repository_bundle_rejects_target_untracked_drift(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project, _, _, _ = ready_project(Path(temporary))
            acquisition = RepositoryAcquirer().acquire(project)
            target = project.root / acquisition.target_worktree.path
            (target / "migration-in-progress.rs").write_text("// work\n", encoding="utf-8")
            with self.assertRaisesRegex(WorkflowError, "current identity"):
                validate_repository_bundle(repository_bundle_context(project))

    def test_repository_bundle_rejects_target_tracked_content_drift(self) -> None:
        for drift in ("modified", "deleted"):
            with self.subTest(drift=drift), tempfile.TemporaryDirectory() as temporary:
                project, _, _, _ = ready_project(Path(temporary))
                acquisition = RepositoryAcquirer().acquire(project)
                target = project.root / acquisition.target_worktree.path
                tracked = target / "README.md"
                if drift == "modified":
                    tracked.write_text("changed after observation\n", encoding="utf-8")
                else:
                    tracked.unlink()
                with self.assertRaisesRegex(WorkflowError, "current identity"):
                    validate_repository_bundle(repository_bundle_context(project))

    def test_untracked_frozen_baseline_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project, _, _, _ = ready_project(Path(temporary))
            acquisition = RepositoryAcquirer().acquire(project)
            source = next(
                record for record in acquisition.checkouts if record.role is RepositoryRole.SOURCE
            )
            (project.root / source.checkout_path / "untracked").write_text(
                "must fail closed\n", encoding="utf-8"
            )
            with self.assertRaisesRegex(WorkflowError, "frozen source repository"):
                validate_repository_bundle(repository_bundle_context(project))

    def test_incomplete_managed_repository_is_quarantined_and_retryable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project, _, _, _ = ready_project(Path(temporary))
            managed = project.control / "git" / "source.git"
            managed.mkdir(parents=True)
            marker = managed / "partial-fetch"
            marker.write_text("preserve me\n", encoding="utf-8")
            RepositoryAcquirer().acquire(project)
            quarantined = list(
                (project.control / "quarantine" / "repositories").glob("source-*.git")
            )
            self.assertEqual(len(quarantined), 1)
            self.assertEqual((quarantined[0] / marker.name).read_text(), "preserve me\n")

    def test_managed_repository_symlink_is_rejected_without_touching_target(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project, _, _, _ = ready_project(root)
            external = root / "external-repository-placeholder"
            external.mkdir()
            marker = external / "owned"
            marker.write_text("outside\n", encoding="utf-8")
            managed = project.control / "git" / "source.git"
            managed.parent.mkdir(parents=True)
            managed.symlink_to(external, target_is_directory=True)
            with self.assertRaisesRegex(WorkflowError, "cannot be a symlink"):
                RepositoryAcquirer().acquire(project)
            self.assertTrue(managed.is_symlink())
            self.assertEqual(marker.read_text(encoding="utf-8"), "outside\n")

    def test_cross_claim_kind_is_derived_from_bindings(self) -> None:
        repositories = [
            {
                "role": role.value,
                "platform": role.value,
                "url": f"https://example.invalid/{role.value}.git",
                "requested_ref": "v1.0.0",
                "selection_rule": "fixture",
            }
            for role in RepositoryRole
        ]
        proposal = {
            "schema_version": 1,
            "migration_envelope_sha256": "1" * 64,
            "repositories": repositories,
            "compatibility_evidence": [
                {
                    "source_url": "https://example.invalid/compatibility",
                    "claim": "generic compatibility statement",
                    "excerpt": "generic compatibility statement",
                    "bindings": [
                        {"role": item["role"], "requested_ref": item["requested_ref"]}
                        for item in repositories
                    ],
                }
            ],
        }
        parsed = RevisionSelectionProposal.from_dict(proposal)
        self.assertEqual(
            parsed.compatibility_evidence[0].claim_kind,
            CompatibilityClaimKind.CROSS_REPOSITORY,
        )

    def test_source_repository_cannot_close_a_target_facet(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project, _, _, _ = ready_project(Path(temporary))
            RepositoryAcquirer().acquire(project)
            target_facet = EvidenceFacet(EvidenceLane.TARGET, TargetFacet.DRIVER_FRAMEWORK)
            proposal = evidence_proposal(
                project,
                {target_facet: (RepositoryRole.SOURCE, "drivers/example.c")},
            )
            with self.assertRaisesRegex(
                WorkflowError,
                "source repository cannot control target/driver_framework",
            ):
                import_evidence_proposal(project, proposal)

    def test_declared_gap_reason_must_match_retrieval_outcome(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project, _, _, _ = ready_project(Path(temporary))
            RepositoryAcquirer().acquire(project)
            proposal = evidence_proposal(project)
            gap = next(
                item
                for item in proposal["facets"]
                if item["disposition"] == FacetDisposition.EXPLICIT_GAP.value
            )
            gap["gap"]["reason"] = GapReason.ACCESS_RESTRICTED.value
            imported = import_evidence_proposal(project, proposal)
            with self.assertRaisesRegex(WorkflowError, "declared gap reason"):
                EvidenceClosureFinalizer().finalize(project, proposal=imported.occurrence)

    def test_revision_citation_excerpt_may_summarize_retrieved_content(self) -> None:
        with compatibility_evidence_server(b"actual compatibility statement\n") as source_url:
            citation = CompatibilityCitation(
                source_url,
                "fixture compatibility",
                "missing quoted statement",
                CompatibilityClaimKind.CROSS_REPOSITORY,
                tuple(ProposedRevisionBinding(role, "v1.0.0") for role in RepositoryRole),
                4096,
            )
            retrieved = RevisionEvidenceRetriever().retrieve((citation,))
            self.assertEqual(retrieved[0].citation.excerpt, "missing quoted statement")
            self.assertEqual(retrieved[0].data, b"actual compatibility statement\n")

    def test_revision_evidence_preflight_precedes_remote_resolution(self) -> None:
        project = Mock()
        project.stage.side_effect = lambda stage: SimpleNamespace(
            status=(
                StageStatus.PASS
                if stage is IntakeStage.ENVELOPE_FREEZE
                else StageStatus.RUNNING
            )
        )
        project.artifact.return_value = SimpleNamespace(digest="1" * 64)
        project.config = SimpleNamespace(
            source_platform="example-source",
            target_platform="example-target",
        )
        repositories = tuple(
            SimpleNamespace(role=role, platform=platform)
            for role, platform in (
                (RepositoryRole.SOURCE, "example-source"),
                (RepositoryRole.TARGET, "example-target"),
                (RepositoryRole.QEMU, "qemu"),
            )
        )
        envelope = SimpleNamespace(
            proposal=SimpleNamespace(
                migration_envelope_sha256="1" * 64,
                repositories=repositories,
                compatibility_evidence=(),
            )
        )
        with (
            patch(
                "driver_port_factory.acquisition.revision_selection.load_revision_proposal",
                return_value=envelope,
            ),
            patch.object(
                RevisionEvidenceRetriever,
                "retrieve",
                side_effect=WorkflowError("evidence preflight failed"),
            ),
            patch(
                "driver_port_factory.acquisition.revision_selection.RevisionResolver"
            ) as resolver,
            self.assertRaisesRegex(WorkflowError, "evidence preflight failed"),
        ):
            RevisionSelector().select(project, proposal=ArtifactOccurrence("0" * 64, 0))
        resolver.assert_not_called()

    def test_derived_material_requires_an_original_parent_and_matching_path(self) -> None:
        cases = (
            ("non-original-parent", self._non_original_parent_locators),
            ("wrong-original-path", self._wrong_original_path_locators),
        )
        for label, locator_factory in cases:
            with self.subTest(case=label), tempfile.TemporaryDirectory() as temporary:
                project, _, _, _ = ready_project(Path(temporary))
                RepositoryAcquirer().acquire(project)
                facet = EvidenceFacet(EvidenceLane.SOURCE, SourceFacet.FRAMEWORK_CONTRACTS)
                proposal = evidence_proposal(
                    project,
                    {facet: (RepositoryRole.SOURCE, "docs/original.txt")},
                )
                item = next(
                    item
                    for item in proposal["facets"]
                    if (item["lane"], item["facet"]) == (facet.lane.value, facet.name)
                )
                item["disposition"] = FacetDisposition.CONTROLLED.value
                item["locators"] = locator_factory(project, facet)
                item.pop("gap", None)
                imported = import_evidence_proposal(project, proposal)
                expected = (
                    "controlled original"
                    if label == "non-original-parent"
                    else "original_path differs"
                )
                with self.assertRaisesRegex(WorkflowError, expected):
                    EvidenceClosureFinalizer().finalize(
                        project,
                        proposal=imported.occurrence,
                    )

    def test_revision_selection_is_rejected_before_scope_freeze(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = initialize_project(Path(temporary) / "run", project_config())
            with self.assertRaises(WorkflowError):
                RevisionSelector().select(project, proposal=ArtifactOccurrence("0" * 64, 0))

    def test_repositories_and_six_lane_evidence_closure_are_verified(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project, _, _, _ = ready_project(Path(temporary))
            acquisition = RepositoryAcquirer().acquire(project)
            close_evidence(project)
            self.assertEqual(
                project.stage(AcquisitionStage.EVIDENCE_CLOSURE).status,
                StageStatus.PASS,
            )
            coverage = project.load_json_artifact(
                AcquisitionStage.EVIDENCE_CLOSURE,
                AcquisitionArtifact.EVIDENCE_COVERAGE_INVENTORY,
            )
            self.assertEqual(len(coverage["facets"]), 6)
            source = next(
                item
                for item in coverage["facets"]
                if (item["lane"], item["facet"])
                == (SOURCE_DRIVER_ENTRY.lane.value, SOURCE_DRIVER_ENTRY.name)
            )
            self.assertEqual(source["disposition"], "CONTROLLED")
            manifest_ref = project.artifact(
                AcquisitionStage.EVIDENCE_CLOSURE,
                AcquisitionArtifact.MATERIALS_MANIFEST,
            )
            materials = project.artifacts.read(manifest_ref).decode("utf-8")
            self.assertNotIn("explicit-evidence-gap", materials)
            self.assertTrue((project.root / acquisition.target_worktree.path / ".git").exists())
            self.assertTrue(AcquisitionVerifier().verify(project)["valid"])
            self.assertEqual(project.stage(EnvironmentStage.RECOVERY).status, StageStatus.READY)

    def test_partial_repository_acquisition_is_idempotently_resumed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project, _, _, _ = ready_project(Path(temporary))
            original = BaselineRepositoryAcquirer.acquire
            failed = False

            def fail_once(acquirer, spec, checkout_name):
                nonlocal failed
                if spec.role.value == "target" and not failed:
                    failed = True
                    raise WorkflowError("injected target clone interruption")
                return original(acquirer, spec, checkout_name)

            with (
                patch.object(BaselineRepositoryAcquirer, "acquire", new=fail_once),
                self.assertRaises(WorkflowError),
            ):
                RepositoryAcquirer().acquire(project)
            self.assertEqual(
                project.stage(AcquisitionStage.REPOSITORY_ACQUISITION).status,
                StageStatus.RUNNING,
            )
            RepositoryAcquirer().acquire(project)
            self.assertEqual(
                project.stage(AcquisitionStage.REPOSITORY_ACQUISITION).status,
                StageStatus.PASS,
            )

    def test_ref_drift_after_selection_fails_closed_and_remains_repairable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project, source, _, _ = ready_project(Path(temporary))
            (source / "drivers/example.c").write_text("changed\n", encoding="utf-8")
            git("add", ".", cwd=source)
            git("commit", "-m", "move ref", cwd=source)
            git("tag", "-f", "v1.0.0", cwd=source)
            with self.assertRaisesRegex(WorkflowError, "does not match planned"):
                RepositoryAcquirer().acquire(project)
            attempt = project.load_json_artifact(
                AcquisitionStage.REPOSITORY_ACQUISITION,
                AcquisitionArtifact.REPOSITORY_ACQUISITION_ATTEMPT,
            )
            self.assertTrue(attempt["partial_paths_preserved"])
            self.assertEqual(
                project.stage(AcquisitionStage.REPOSITORY_ACQUISITION).status,
                StageStatus.RUNNING,
            )

    def test_codex_result_alone_cannot_finalize_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project, _, _, _ = ready_project(Path(temporary))
            RepositoryAcquirer().acquire(project)
            project.start(AcquisitionStage.EVIDENCE_CLOSURE)
            self.assertEqual(
                project.stage(AcquisitionStage.EVIDENCE_CLOSURE).status,
                StageStatus.RUNNING,
            )
            with self.assertRaises(WorkflowError):
                EvidenceClosureFinalizer().finalize(
                    project,
                    proposal=ArtifactOccurrence("0" * 64, 0),
                )

    def test_missing_facet_is_rejected_during_typed_proposal_import(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project, _, _, _ = ready_project(Path(temporary))
            RepositoryAcquirer().acquire(project)
            project.start(AcquisitionStage.EVIDENCE_CLOSURE)
            invalid = {
                "schema_version": 1,
                "migration_envelope_sha256": "0" * 64,
                "repository_manifest_sha256": "0" * 64,
                "facets": [],
            }
            ref = project.record_artifact(
                AcquisitionStage.EVIDENCE_CLOSURE,
                GeneratedArtifact(
                    CodexArtifact.JOB_RESULT,
                    json.dumps(invalid).encode(),
                    "test:invalid-proposal",
                ),
            )
            assert ref.ordinal is not None
            with self.assertRaisesRegex(WorkflowError, "all six evidence domains"):
                EvidenceProposalImporter().import_job_result(
                    project, job_digest=ref.digest, job_ordinal=ref.ordinal
                )

    def test_path_escape_is_rejected_before_materialization(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project, _, _, _ = ready_project(Path(temporary))
            RepositoryAcquirer().acquire(project)
            proposal = evidence_proposal(project)
            entry = next(
                item
                for item in proposal["facets"]
                if item["lane"] == EvidenceLane.SOURCE.value
                and item["facet"] == SourceFacet.DRIVER_ENTRY.value
            )
            entry["locators"][0]["path"] = "../outside.c"
            with self.assertRaisesRegex(WorkflowError, "workspace-relative"):
                import_evidence_proposal(project, proposal)

    def test_external_reference_cannot_satisfy_a_controlled_facet(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project, _, _, _ = ready_project(Path(temporary))
            RepositoryAcquirer().acquire(project)
            proposal = evidence_proposal(project)
            framework = next(
                item for item in proposal["facets"] if item["lane"] == EvidenceLane.HARDWARE.value
            )
            framework["disposition"] = FacetDisposition.CONTROLLED.value
            framework.pop("gap")
            framework["locators"] = [
                {
                    "kind": LocatorKind.EXTERNAL_REFERENCE.value,
                    "source_url": "https://evidence.example.invalid/repository-lock",
                    "max_bytes": 4096,
                }
            ]
            with self.assertRaisesRegex(WorkflowError, "content-bound locators"):
                import_evidence_proposal(project, proposal)
            self.assertEqual(
                project.stage(AcquisitionStage.EVIDENCE_CLOSURE).status,
                StageStatus.RUNNING,
            )

    def test_repository_endorsement_controls_cross_host_external_release(self) -> None:
        content = b"official target release instructions\n"
        with (
            compatibility_evidence_server(content) as source_url,
            tempfile.TemporaryDirectory() as temporary,
        ):
            project, _, _, _ = ready_project(
                Path(temporary),
                target_overrides={"release-source.txt": f"release: {source_url}\n"},
            )
            RepositoryAcquirer().acquire(project)
            facet = EvidenceFacet(EvidenceLane.TARGET, TargetFacet.RELEASE_ASSETS)
            proposal = evidence_proposal(project)
            set_external_proposal(
                proposal,
                facet=facet,
                source_url=source_url,
                content=content,
                authority={
                    "kind": "repository_endorsement",
                    "repository": RepositoryRole.TARGET.value,
                    "path": "release-source.txt",
                    "line_start": 1,
                    "line_end": 1,
                },
            )
            imported = import_evidence_proposal(project, proposal)
            EvidenceClosureFinalizer().finalize(project, proposal=imported.occurrence)
            materials = [
                json.loads(line)
                for line in project.artifacts.read(
                    project.artifact(
                        AcquisitionStage.EVIDENCE_CLOSURE,
                        AcquisitionArtifact.MATERIALS_MANIFEST,
                    )
                ).splitlines()
            ]
            external = next(item for item in materials if item["facet"] == facet.name)
            self.assertTrue(external["path"].startswith(".dpf/cas/objects/sha256/"))
            self.assertFalse((project.root / ".dpf" / "evidence").exists())
            content_ref = external["origin"]["response"]["content_ref"]
            self.assertIsInstance(content_ref["ordinal"], int)
            self.assertEqual(content_ref["source"], source_url)
            validate_evidence_closure_bundle(evidence_bundle_context(project))

    def test_repository_hostname_without_endorsement_cannot_establish_authority(self) -> None:
        content = b"unendorsed target release instructions\n"
        with (
            compatibility_evidence_server(content) as source_url,
            tempfile.TemporaryDirectory() as temporary,
        ):
            project, _, _, _ = ready_project(Path(temporary))
            RepositoryAcquirer().acquire(project)
            facet = EvidenceFacet(EvidenceLane.TARGET, TargetFacet.RELEASE_ASSETS)
            proposal = evidence_proposal(project)
            set_external_proposal(
                proposal,
                facet=facet,
                source_url=source_url,
                content=content,
                authority={
                    "kind": "repository_endorsement",
                    "repository": RepositoryRole.TARGET.value,
                    "path": "README.md",
                    "line_start": 1,
                    "line_end": 1,
                },
            )
            imported = import_evidence_proposal(project, proposal)
            with self.assertRaisesRegex(WorkflowError, "controlled facet"):
                EvidenceClosureFinalizer().finalize(
                    project,
                    proposal=imported.occurrence,
                )
            self.assertFalse((project.root / ".dpf" / "evidence").exists())

    def test_partial_corroboration_records_attempt_content_without_material_claim(self) -> None:
        content = b"hardware note requiring corroboration\n"
        with (
            compatibility_evidence_server(content) as primary_url,
            compatibility_evidence_server(content) as corroboration_server_url,
            tempfile.TemporaryDirectory() as temporary,
        ):
            corroboration_url = corroboration_server_url.replace("127.0.0.1", "localhost")
            project, _, _, _ = ready_project(Path(temporary))
            RepositoryAcquirer().acquire(project)
            facet = EvidenceFacet(EvidenceLane.HARDWARE, HardwareFacet.DEVICE_MANUAL)
            proposal = evidence_proposal(project)
            set_external_proposal(
                proposal,
                facet=facet,
                source_url=primary_url,
                content=content,
                authority={
                    "kind": "corroborated",
                    "sources": [
                        {
                            "source_url": corroboration_url,
                            "expected_sha256": "0" * 64,
                            "max_bytes": 4096,
                        }
                    ],
                },
                disposition=FacetDisposition.EXPLICIT_GAP,
            )
            imported = import_evidence_proposal(project, proposal)
            EvidenceClosureFinalizer().finalize(project, proposal=imported.occurrence)
            ledger = project.load_json_artifact(
                AcquisitionStage.EVIDENCE_CLOSURE,
                AcquisitionArtifact.EVIDENCE_RETRIEVAL_LEDGER,
            )
            attempt = next(
                item
                for item in ledger["attempts"]
                if (item["lane"], item["facet"]) == (facet.lane.value, facet.name)
            )
            self.assertEqual(attempt["outcome"], "CONFLICT")
            self.assertEqual(len(attempt["content_refs"]), 2)
            self.assertEqual(attempt["material_ids"], [])
            validate_evidence_closure_bundle(evidence_bundle_context(project))

    def test_http_content_reference_is_exact_and_cannot_be_swapped(self) -> None:
        content = b"corroborated hardware contract\n"
        with (
            compatibility_evidence_server(content) as primary_url,
            compatibility_evidence_server(content) as corroboration_server_url,
            tempfile.TemporaryDirectory() as temporary,
        ):
            corroboration_url = corroboration_server_url.replace("127.0.0.1", "localhost")
            project, _, _, _ = ready_project(Path(temporary))
            RepositoryAcquirer().acquire(project)
            facet = EvidenceFacet(EvidenceLane.HARDWARE, HardwareFacet.DEVICE_MANUAL)
            proposal = evidence_proposal(project)
            set_external_proposal(
                proposal,
                facet=facet,
                source_url=primary_url,
                content=content,
                authority={
                    "kind": "corroborated",
                    "sources": [
                        {
                            "source_url": corroboration_url,
                            "expected_sha256": hashlib.sha256(content).hexdigest(),
                            "max_bytes": 4096,
                        }
                    ],
                },
            )
            imported = import_evidence_proposal(project, proposal)
            EvidenceClosureFinalizer().finalize(project, proposal=imported.occurrence)
            manifest_ref = project.artifact(
                AcquisitionStage.EVIDENCE_CLOSURE,
                AcquisitionArtifact.MATERIALS_MANIFEST,
            )
            records = [
                json.loads(line) for line in project.artifacts.read(manifest_ref).splitlines()
            ]
            external = next(record for record in records if record["facet"] == facet.name)
            primary_ref = external["origin"]["response"]["content_ref"]
            corroboration_ref = external["origin"]["corroboration"][0]["content_ref"]

            tampered = json.loads(json.dumps(records))
            tampered_external = next(
                record for record in tampered if record["facet"] == facet.name
            )
            tampered_external["origin"]["response"]["content_ref"]["ordinal"] += 1000
            tampered_data = b"".join(
                json.dumps(record, sort_keys=True).encode() + b"\n" for record in tampered
            )
            with self.assertRaisesRegex(WorkflowError, "exact HTTP occurrences"):
                validate_evidence_closure_bundle(
                    evidence_bundle_context(
                        project,
                        overrides={AcquisitionArtifact.MATERIALS_MANIFEST: tampered_data},
                    )
                )

            swapped = json.loads(json.dumps(records))
            swapped_external = next(
                record for record in swapped if record["facet"] == facet.name
            )
            swapped_external["origin"]["response"]["content_ref"] = corroboration_ref
            swapped_external["origin"]["corroboration"][0]["content_ref"] = primary_ref
            swapped_data = b"".join(
                json.dumps(record, sort_keys=True).encode() + b"\n" for record in swapped
            )
            with self.assertRaisesRegex(WorkflowError, "exact HTTP occurrences"):
                validate_evidence_closure_bundle(
                    evidence_bundle_context(
                        project,
                        overrides={AcquisitionArtifact.MATERIALS_MANIFEST: swapped_data},
                    )
                )

            with self.assertRaisesRegex(WorkflowError, "exact auxiliary occurrence"):
                validate_evidence_closure_bundle(
                    evidence_bundle_context(
                        project,
                        excluded_auxiliary_ordinals=frozenset({primary_ref["ordinal"]}),
                    )
                )

    def test_git_blob_retrieval_rejects_dirty_file_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project, _, _, _ = ready_project(Path(temporary))
            acquisition = RepositoryAcquirer().acquire(project)
            source = next(
                checkout for checkout in acquisition.checkouts if checkout.role.value == "source"
            )
            entry = project.root / source.checkout_path / "drivers/example.c"
            entry.write_text("dirty bytes\n", encoding="utf-8")
            locator = {
                "kind": LocatorKind.GIT_BLOB.value,
                "repository": "source",
                "path": "drivers/example.c",
                "license": "review-required",
                "redistribution": MaterialRedistribution.UNKNOWN.value,
                "original": True,
            }
            with self.assertRaisesRegex(RetrievalFailure, "differ from Git blob"):
                EvidenceRetriever(project, load_repository_acquisition(project)).retrieve(
                    SOURCE_DRIVER_ENTRY,
                    parse_locator(locator),
                    material_id="source.driver_entry.test",
                )

    def test_hash_failure_rolls_back_required_bundle_and_can_retry(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project, _, _, _ = ready_project(Path(temporary))
            RepositoryAcquirer().acquire(project)
            imported = import_evidence_proposal(project, evidence_proposal(project))
            original = EvidenceRetriever.retrieve

            def corrupt_hash(retriever, facet, locator, *, material_id):
                retrieved = original(retriever, facet, locator, material_id=material_id)
                if facet == SOURCE_DRIVER_ENTRY:
                    return RetrievedMaterial(
                        replace(retrieved.record, sha256="0" * 64), retrieved.detail
                    )
                return retrieved

            with (
                patch.object(EvidenceRetriever, "retrieve", new=corrupt_hash),
                self.assertRaisesRegex(WorkflowError, "bytes drifted"),
            ):
                EvidenceClosureFinalizer().finalize(project, proposal=imported.occurrence)
            required = {
                item.value
                for item in project.stage(AcquisitionStage.EVIDENCE_CLOSURE).required_outputs
            }
            self.assertTrue(
                required.isdisjoint(
                    ref.kind
                    for ref in project.artifact_refs(stage=AcquisitionStage.EVIDENCE_CLOSURE)
                )
            )
            self.assertFalse((project.root / "knowledge" / "manifests").exists())
            EvidenceClosureFinalizer().finalize(project, proposal=imported.occurrence)
            self.assertEqual(
                project.stage(AcquisitionStage.EVIDENCE_CLOSURE).status,
                StageStatus.PASS,
            )

    @staticmethod
    def _source_blob(path: str, *, original: bool) -> dict[str, object]:
        return {
            "kind": LocatorKind.GIT_BLOB.value,
            "repository": RepositoryRole.SOURCE.value,
            "path": path,
            "license": "review-required",
            "redistribution": MaterialRedistribution.UNKNOWN.value,
            "original": original,
        }

    @classmethod
    def _wrong_original_path_locators(
        cls,
        project,
        facet: EvidenceFacet,
    ) -> list[dict[str, object]]:
        original_path = "docs/original.txt"
        derived_path = "docs/derived.txt"
        original = cls._source_blob(original_path, original=True)
        original_id = material_identifier(facet, parse_locator(original))
        derived = {
            **cls._source_blob(derived_path, original=False),
            "derived_from": original_id,
            "original_path": "evidence/not-the-parent.txt",
            "page_map": "page 1 -> line 1",
        }
        return [original, derived]

    @classmethod
    def _non_original_parent_locators(
        cls,
        project,
        facet: EvidenceFacet,
    ) -> list[dict[str, object]]:
        parent_path = "docs/original.txt"
        child_path = "docs/derived.txt"
        parent = {
            **cls._source_blob(parent_path, original=False),
            "derived_from": "uncontrolled-parent",
            "original_path": "evidence/uncontrolled.txt",
            "page_map": "page 1 -> line 1",
        }
        parent_id = material_identifier(facet, parse_locator(parent))
        child = {
            **cls._source_blob(child_path, original=False),
            "derived_from": parent_id,
            "original_path": parent_path,
            "page_map": "page 1 -> line 1",
        }
        return [child, parent]


if __name__ == "__main__":
    unittest.main()
