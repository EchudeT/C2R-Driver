from __future__ import annotations

import hashlib
import json
from contextlib import suppress
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from ..core.models import ActorRole, StageStatus, WorkflowError, utc_now
from ..core.project import Project
from ..core.store import canonical_json
from ..intake.resolver import SourceEntryVerifier
from .git import GitAcquirer
from .models import AcquisitionPlan, CheckoutRecord, RepositoryRole, RepositorySpec
from .registry import RepositoryLocator, RepositoryRegistry


def _record_acquisition_failure(function):
    def wrapped(self, project: Project, *args, **kwargs):
        try:
            return function(self, project, *args, **kwargs)
        except Exception as error:
            with suppress(WorkflowError, OSError):
                if project.store.stage("evidence_acquisition").status is StageStatus.RUNNING:
                    self._add_json(
                        project,
                        "evidence_acquisition",
                        "acquisition_failure",
                        {
                            "schema_version": 1,
                            "failure_type": type(error).__name__,
                            "message": str(error),
                            "recorded_at": utc_now(),
                            "partial_paths_preserved": True,
                        },
                    )
                    project.complete("evidence_acquisition", StageStatus.FAIL, message=str(error))
            raise

    return wrapped


@dataclass(frozen=True, slots=True)
class AcquisitionResult:
    status: StageStatus
    checkouts: tuple[CheckoutRecord, ...]
    target_worktree: str
    source_identity_consistent: bool


class AcquisitionService:
    ROLES = (ActorRole.DEVELOPER, ActorRole.MIGRATION_OPERATOR)

    def plan(
        self,
        project: Project,
        *,
        source_url: str | None = None,
        source_ref: str | None = None,
        target_url: str | None = None,
        target_ref: str | None = None,
        qemu_url: str | None = None,
        qemu_ref: str | None = None,
        registry_path: Path | None = None,
    ) -> AcquisitionPlan:
        project.ensure_role(*self.ROLES)
        if project.store.stage("migration_envelope_freeze").status is not StageStatus.PASS:
            raise WorkflowError("acquisition planning requires a frozen migration envelope")
        if project.store.stage("revision_selection").status is not StageStatus.READY:
            raise WorkflowError("revision_selection is not READY")
        registry = RepositoryRegistry(registry_path)
        source = self._locator(
            registry,
            project.config.source_platform,
            source_url,
            source_ref,
        )
        target = self._locator(
            registry,
            project.config.target_platform,
            target_url,
            target_ref,
        )
        qemu = self._locator(registry, "qemu", qemu_url, qemu_ref)
        git = GitAcquirer(project.root, project.control)
        repositories = (
            git.resolve_ref(
                role=RepositoryRole.SOURCE,
                platform=project.config.source_platform,
                url=source.url,
                requested_ref=source.default_ref,
                selection_rule=source.selection_rule,
            ),
            git.resolve_ref(
                role=RepositoryRole.TARGET,
                platform=project.config.target_platform,
                url=target.url,
                requested_ref=target.default_ref,
                selection_rule=target.selection_rule,
            ),
            git.resolve_ref(
                role=RepositoryRole.QEMU,
                platform="qemu",
                url=qemu.url,
                requested_ref=qemu.default_ref,
                selection_rule=qemu.selection_rule,
            ),
        )
        envelope_ref = project.artifact("migration_envelope_freeze", "migration_envelope")
        plan = AcquisitionPlan(
            schema_version=1,
            repositories=repositories,
            migration_envelope_digest=envelope_ref.digest,
            planned_at=utc_now(),
        )
        plan_document = plan.to_dict()
        plan_document["resolution_commands"] = git.serialized_commands()
        revision_manifest = {
            "schema_version": 1,
            **{
                spec.role.value: {
                    "platform": spec.platform,
                    "url": spec.url,
                    "requested_ref": spec.requested_ref,
                    "revision": spec.resolved_commit,
                    "selection_rule": spec.selection_rule,
                }
                for spec in repositories
            },
        }
        project.start("revision_selection")
        self._add_json(
            project,
            "revision_selection",
            "revision_manifest",
            revision_manifest,
        )
        self._add_json(
            project,
            "revision_selection",
            "acquisition_plan",
            plan_document,
        )
        project.complete("revision_selection", StageStatus.PASS)
        return plan

    @_record_acquisition_failure
    def acquire(self, project: Project) -> AcquisitionResult:
        project.ensure_role(*self.ROLES)
        if project.store.stage("evidence_acquisition").status is not StageStatus.READY:
            raise WorkflowError("evidence_acquisition is not READY")
        plan = AcquisitionPlan.from_dict(
            project.load_json_artifact("revision_selection", "acquisition_plan")
        )
        current_envelope_ref = project.artifact("migration_envelope_freeze", "migration_envelope")
        if current_envelope_ref.digest != plan.migration_envelope_digest:
            raise WorkflowError("migration envelope changed after acquisition planning")
        project.start("evidence_acquisition")
        git = GitAcquirer(project.root, project.control)
        checkout_names = {
            RepositoryRole.SOURCE: "source-baseline",
            RepositoryRole.TARGET: "target-baseline",
            RepositoryRole.QEMU: "qemu-baseline",
        }
        checkouts = tuple(
            git.acquire(spec, checkout_names[spec.role]) for spec in plan.repositories
        )
        target_spec = self._spec(plan, RepositoryRole.TARGET)
        target_worktree = git.create_target_worktree(target_spec, project.config.project_id)
        source_record = self._record(checkouts, RepositoryRole.SOURCE)
        source_root = project.root / source_record.checkout_path
        envelope = project.load_json_artifact("migration_envelope_freeze", "migration_envelope")
        identity = SourceEntryVerifier().verify(envelope, source_root)
        materials = self._materials(project, checkouts, envelope)
        acquired_at = utc_now()
        manifest = {
            "schema_version": 1,
            "project_id": project.config.project_id,
            "acquired_at": acquired_at,
            "migration_envelope_sha256": plan.migration_envelope_digest,
            "checkouts": [record.to_dict() for record in checkouts],
            "target_worktree": target_worktree,
            "source_identity_verification": asdict(identity),
            "commands": git.serialized_commands(),
            "coverage_inventory": {
                "source": "ACQUIRED",
                "target": "ACQUIRED",
                "qemu": "ACQUIRED",
                "hardware": "GAP",
                "tests": "GAP",
                "tooling": "GAP",
            },
            "execution_policy": "downloaded repository content was not executed",
        }
        manifest_dir = project.control / "manifests"
        manifest_dir.mkdir(parents=True, exist_ok=True)
        acquisition_path = manifest_dir / "acquisition.json"
        acquisition_path.write_text(
            json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        knowledge_manifests = project.root / "knowledge" / "manifests"
        knowledge_manifests.mkdir(parents=True, exist_ok=True)
        materials_path = knowledge_manifests / "materials.jsonl"
        materials_path.write_text(
            "".join(canonical_json(material) + "\n" for material in materials),
            encoding="utf-8",
        )
        identity_bytes = (
            json.dumps(asdict(identity), ensure_ascii=False, sort_keys=True, indent=2) + "\n"
        ).encode("utf-8")
        project.add_artifact("evidence_acquisition", "acquisition_manifest", acquisition_path)
        project.add_artifact("evidence_acquisition", "materials_manifest", materials_path)
        project.add_bytes(
            "evidence_acquisition",
            "source_identity_verification",
            identity_bytes,
            source="generated:source-entry-verifier",
        )
        outcome = StageStatus.PASS if identity.consistent else StageStatus.FAIL
        project.complete(
            "evidence_acquisition",
            outcome,
            message=None if identity.consistent else "; ".join(identity.conflicts),
        )
        return AcquisitionResult(
            status=outcome,
            checkouts=checkouts,
            target_worktree=target_worktree,
            source_identity_consistent=identity.consistent,
        )

    def verify(self, project: Project) -> dict[str, Any]:
        manifest = project.load_json_artifact("evidence_acquisition", "acquisition_manifest")
        git = GitAcquirer(project.root, project.control)
        results = [git.verify(CheckoutRecord.from_dict(record)) for record in manifest["checkouts"]]
        return {
            "project_id": project.config.project_id,
            "repositories": results,
            "valid": all(result["valid"] for result in results),
        }

    @staticmethod
    def _locator(
        registry: RepositoryRegistry,
        platform: str,
        explicit_url: str | None,
        explicit_ref: str | None,
    ) -> RepositoryLocator:
        registered = None
        try:
            registered = registry.lookup(platform)
        except WorkflowError:
            if explicit_url is None:
                raise
        if explicit_url is None:
            assert registered is not None
            url = registered.url
        else:
            local = Path(explicit_url).expanduser()
            url = str(local.resolve()) if local.exists() else explicit_url
        ref = explicit_ref or (registered.default_ref if registered else None)
        if not ref:
            raise WorkflowError(f"an explicit ref is required for repository {url}")
        rule = (
            registered.selection_rule
            if registered
            else "resolve the supplied ref once, then freeze the full commit"
        )
        return RepositoryLocator(platform, url, ref, rule)

    @staticmethod
    def _spec(plan: AcquisitionPlan, role: RepositoryRole) -> RepositorySpec:
        matches = [spec for spec in plan.repositories if spec.role is role]
        if len(matches) != 1:
            raise WorkflowError(f"acquisition plan requires exactly one {role.value} repository")
        return matches[0]

    @staticmethod
    def _record(records: tuple[CheckoutRecord, ...], role: RepositoryRole) -> CheckoutRecord:
        matches = [record for record in records if record.role is role]
        if len(matches) != 1:
            raise WorkflowError(f"acquisition result requires exactly one {role.value} checkout")
        return matches[0]

    @staticmethod
    def _materials(
        project: Project,
        checkouts: tuple[CheckoutRecord, ...],
        envelope: dict[str, Any],
    ) -> list[dict[str, Any]]:
        materials: list[dict[str, Any]] = []
        for record in checkouts:
            materials.append(
                {
                    "id": f"{record.role.value}-repository-lock",
                    "domain": record.role.value,
                    "path": record.checkout_path,
                    "source_url": record.source_url,
                    "revision": record.resolved_commit,
                    "acquired_at": record.acquired_at,
                    "license": "review-required",
                    "redistribution": "unknown",
                    "sha256": record.lock_sha256,
                    "original": True,
                    "notes": f"locked Git tree {record.tree_id}; full tree content is not represented by a mutable directory name",
                }
            )
        source = AcquisitionService._record(checkouts, RepositoryRole.SOURCE)
        entry_relative = str(envelope["source_driver_entry_or_repository_hint"])
        entry = project.root / source.checkout_path / entry_relative
        if entry.is_file():
            digest = hashlib.sha256(entry.read_bytes()).hexdigest()
            materials.append(
                {
                    "id": "source-driver-entry",
                    "domain": "source",
                    "path": str(entry.relative_to(project.root)),
                    "source_url": source.source_url,
                    "revision": source.resolved_commit,
                    "acquired_at": source.acquired_at,
                    "license": "review-required",
                    "redistribution": "unknown",
                    "sha256": digest,
                    "original": True,
                    "notes": "frozen driver entry; the recursive source closure is produced later",
                }
            )
        return materials

    @staticmethod
    def _add_json(project: Project, stage: str, kind: str, value: dict[str, Any]) -> None:
        project.add_bytes(
            stage,
            kind,
            (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode(
                "utf-8"
            ),
            source=f"generated:acquisition:{kind}",
        )
