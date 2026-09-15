from __future__ import annotations

import json
from pathlib import Path

from ..core.models import ActorRole, GeneratedArtifact, StageStatus, WorkflowError, utc_now
from ..core.project import Project
from ..intake.contracts import IntakeArtifact, IntakeStage
from .contracts import AcquisitionArtifact, AcquisitionStage
from .git import GitAcquirer
from .models import AcquisitionPlan, RepositoryRole
from .registry import RepositoryLocator, RepositoryRegistry


class AcquisitionPlanner:
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
        if project.stage(IntakeStage.ENVELOPE_FREEZE).status is not StageStatus.PASS:
            raise WorkflowError("acquisition planning requires a frozen migration envelope")
        if project.stage(AcquisitionStage.REVISION_SELECTION).status is not StageStatus.READY:
            raise WorkflowError("revision_selection is not READY")
        registry = RepositoryRegistry(registry_path)
        locations = (
            self._locator(registry, project.config.source_platform, source_url, source_ref),
            self._locator(registry, project.config.target_platform, target_url, target_ref),
            self._locator(registry, "qemu", qemu_url, qemu_ref),
        )
        roles = (RepositoryRole.SOURCE, RepositoryRole.TARGET, RepositoryRole.QEMU)
        git = GitAcquirer(project.root, project.control)
        repositories = tuple(
            git.resolve_ref(
                role=role,
                platform=location.canonical_platform,
                url=location.url,
                requested_ref=location.default_ref,
                selection_rule=location.selection_rule,
            )
            for role, location in zip(roles, locations, strict=True)
        )
        envelope_ref = project.artifact(
            IntakeStage.ENVELOPE_FREEZE, IntakeArtifact.MIGRATION_ENVELOPE
        )
        plan = AcquisitionPlan(1, repositories, envelope_ref.digest, utc_now())
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
        project.start(AcquisitionStage.REVISION_SELECTION)
        project.finalize_stage(
            AcquisitionStage.REVISION_SELECTION,
            (
                self._artifact(AcquisitionArtifact.REVISION_MANIFEST, revision_manifest),
                self._artifact(AcquisitionArtifact.ACQUISITION_PLAN, plan_document),
            ),
        )
        return plan

    @staticmethod
    def _locator(
        registry: RepositoryRegistry,
        platform: str,
        explicit_url: str | None,
        explicit_ref: str | None,
    ) -> RepositoryLocator:
        try:
            registered = registry.lookup(platform)
        except WorkflowError:
            if explicit_url is None:
                raise
            registered = None
        if explicit_url is None:
            if registered is None:
                raise WorkflowError(f"repository registry has no platform {platform}")
            url = registered.url
        else:
            local = Path(explicit_url).expanduser()
            url = str(local.resolve()) if local.exists() else explicit_url
        reference = explicit_ref or (registered.default_ref if registered else None)
        if not reference:
            raise WorkflowError(f"an explicit ref is required for repository {url}")
        rule = (
            registered.selection_rule
            if registered
            else "resolve the supplied ref once, then freeze the full commit"
        )
        return RepositoryLocator(platform, url, reference, rule)

    @staticmethod
    def _artifact(kind: AcquisitionArtifact, value: dict[str, object]) -> GeneratedArtifact:
        data = (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()
        return GeneratedArtifact(kind, data, f"generated:acquisition:{kind.value}")
