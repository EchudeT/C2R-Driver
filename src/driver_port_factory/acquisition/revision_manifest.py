from __future__ import annotations

from dataclasses import dataclass

from ..core.models import WorkflowError
from .commands import RepositoryCommandRecord
from .job import ArtifactOccurrence, JobResultBinding
from .parsing import exact_object, nonempty, object_id, schema_version, sha256
from .repository_role import RepositoryRole
from .repository_spec import RepositorySpec


@dataclass(frozen=True, slots=True)
class RepositoryPlan:
    schema_version: int
    repositories: tuple[RepositorySpec, ...]
    revision_proposal: ArtifactOccurrence
    selection_job: JobResultBinding
    migration_envelope_digest: str
    resolution_commands: tuple[RepositoryCommandRecord, ...]
    planned_at: str

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "repositories": [repository.to_dict() for repository in self.repositories],
            "revision_proposal": {
                "digest": self.revision_proposal.digest,
                "ordinal": self.revision_proposal.ordinal,
            },
            "selection_job": self.selection_job.to_dict(),
            "migration_envelope_digest": self.migration_envelope_digest,
            "resolution_commands": [command.to_dict() for command in self.resolution_commands],
            "planned_at": self.planned_at,
        }

    @classmethod
    def from_dict(cls, value: object) -> RepositoryPlan:
        candidate = exact_object(
            value,
            required={
                "schema_version",
                "repositories",
                "revision_proposal",
                "selection_job",
                "migration_envelope_digest",
                "resolution_commands",
                "planned_at",
            },
            label="repository plan",
        )
        schema_version(candidate, "repository plan")
        repositories = candidate["repositories"]
        commands = candidate["resolution_commands"]
        if not isinstance(repositories, list):
            raise WorkflowError("repository plan collections must be lists")
        if not isinstance(commands, list) or not commands:
            raise WorkflowError("repository plan requires resolution command evidence")
        proposal = exact_object(
            candidate["revision_proposal"],
            required={"digest", "ordinal"},
            label="repository plan proposal occurrence",
        )
        ordinal = proposal["ordinal"]
        if not isinstance(ordinal, int) or ordinal < 0:
            raise WorkflowError("repository plan proposal ordinal is invalid")
        return cls(
            1,
            tuple(
                sorted(
                    (RepositorySpec.from_dict(repository) for repository in repositories),
                    key=lambda repository: repository.role.sequence,
                )
            ),
            ArtifactOccurrence(
                sha256(proposal["digest"], "repository plan proposal digest"),
                ordinal,
            ),
            JobResultBinding.from_dict(candidate["selection_job"]),
            sha256(candidate["migration_envelope_digest"], "repository plan envelope digest"),
            tuple(RepositoryCommandRecord.from_dict(command) for command in commands),
            nonempty(candidate["planned_at"], "repository plan timestamp"),
        )


@dataclass(frozen=True, slots=True)
class RevisionManifest:
    migration_envelope_sha256: str
    revision_proposal: ArtifactOccurrence
    selection_job: JobResultBinding
    repositories: tuple[RepositorySpec, ...]
    schema_version = 1

    @classmethod
    def from_dict(cls, value: object) -> RevisionManifest:
        candidate = exact_object(
            value,
            required={
                "schema_version",
                "migration_envelope_sha256",
                "revision_proposal",
                "selection_job",
                *(role.value for role in RepositoryRole),
            },
            label="revision manifest",
        )
        schema_version(candidate, "revision manifest")
        proposal = exact_object(
            candidate["revision_proposal"],
            required={"digest", "ordinal"},
            label="revision manifest proposal occurrence",
        )
        ordinal = proposal["ordinal"]
        if not isinstance(ordinal, int) or ordinal < 0:
            raise WorkflowError("revision manifest proposal ordinal is invalid")
        repositories: list[RepositorySpec] = []
        for role in sorted(RepositoryRole, key=lambda item: item.sequence):
            item = exact_object(
                candidate[role.value],
                required={"platform", "url", "requested_ref", "revision", "selection_rule"},
                label=f"revision manifest {role.value}",
            )
            repositories.append(
                RepositorySpec(
                    role,
                    nonempty(item["platform"], "revision platform"),
                    nonempty(item["url"], "revision URL"),
                    nonempty(item["requested_ref"], "revision requested ref"),
                    object_id(item["revision"], "revision commit"),
                    nonempty(item["selection_rule"], "revision selection rule"),
                )
            )
        return cls(
            sha256(candidate["migration_envelope_sha256"], "revision envelope SHA256"),
            ArtifactOccurrence(
                sha256(proposal["digest"], "revision proposal digest"),
                ordinal,
            ),
            JobResultBinding.from_dict(candidate["selection_job"]),
            tuple(repositories),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "migration_envelope_sha256": self.migration_envelope_sha256,
            "revision_proposal": {
                "digest": self.revision_proposal.digest,
                "ordinal": self.revision_proposal.ordinal,
            },
            "selection_job": self.selection_job.to_dict(),
            **{
                repository.role.value: {
                    "platform": repository.platform,
                    "url": repository.url,
                    "requested_ref": repository.requested_ref,
                    "revision": repository.resolved_commit,
                    "selection_rule": repository.selection_rule,
                }
                for repository in self.repositories
            },
        }
