from __future__ import annotations

import hashlib
import subprocess
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path, PurePosixPath

from ..core.models import WorkflowError, utc_now
from .parsing import exact_object, nonempty, object_id, schema_version, sha256, string_tuple
from .repository_checkout import CheckoutRecord
from .repository_role import RepositoryRole


class SourceIdentityStatus(StrEnum):
    VERIFIED = "VERIFIED"


@dataclass(frozen=True, slots=True)
class ObservedSourceIdentity:
    repository: RepositoryRole
    resolved_commit: str
    blob: str
    sha256: str
    size_bytes: int

    @classmethod
    def from_dict(cls, value: object) -> ObservedSourceIdentity:
        candidate = exact_object(
            value,
            required={"repository", "resolved_commit", "blob", "sha256", "size_bytes"},
            label="observed source identity",
        )
        try:
            repository = RepositoryRole(candidate["repository"])
        except (TypeError, ValueError) as error:
            raise WorkflowError("observed source identity has an invalid repository") from error
        size = candidate["size_bytes"]
        if not isinstance(size, int) or size <= 0:
            raise WorkflowError("observed source identity requires non-empty source bytes")
        return cls(
            repository,
            object_id(candidate["resolved_commit"], "observed source commit"),
            object_id(candidate["blob"], "observed source blob"),
            sha256(candidate["sha256"], "observed source SHA256"),
            size,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "repository": self.repository.value,
            "resolved_commit": self.resolved_commit,
            "blob": self.blob,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
        }


@dataclass(frozen=True, slots=True)
class SourceIdentityRecord:
    migration_envelope_sha256: str
    source_root: str
    source_entry: str
    observed_identity: ObservedSourceIdentity
    status: SourceIdentityStatus
    observations: tuple[str, ...]
    conflicts: tuple[str, ...]
    verified_at: str
    schema_version = 1

    @classmethod
    def from_dict(cls, value: object) -> SourceIdentityRecord:
        candidate = exact_object(
            value,
            required={
                "schema_version",
                "migration_envelope_sha256",
                "source_root",
                "source_entry",
                "observed_identity",
                "status",
                "observations",
                "conflicts",
                "verified_at",
            },
            label="source identity record",
        )
        schema_version(candidate, "source identity record")
        try:
            status = SourceIdentityStatus(candidate["status"])
        except (TypeError, ValueError) as error:
            raise WorkflowError("source identity record has an invalid status") from error
        observations = string_tuple(
            candidate["observations"],
            "source identity observations",
            allow_empty=False,
        )
        conflicts = string_tuple(candidate["conflicts"], "source identity conflicts")
        if status is SourceIdentityStatus.VERIFIED and conflicts:
            raise WorkflowError("verified source identity cannot contain conflicts")
        return cls(
            sha256(candidate["migration_envelope_sha256"], "source identity envelope"),
            nonempty(candidate["source_root"], "source identity root"),
            _relative_path(candidate["source_entry"]),
            ObservedSourceIdentity.from_dict(candidate["observed_identity"]),
            status,
            observations,
            conflicts,
            nonempty(candidate["verified_at"], "source identity timestamp"),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "migration_envelope_sha256": self.migration_envelope_sha256,
            "source_root": self.source_root,
            "source_entry": self.source_entry,
            "observed_identity": self.observed_identity.to_dict(),
            "status": self.status.value,
            "observations": list(self.observations),
            "conflicts": list(self.conflicts),
            "verified_at": self.verified_at,
        }


class SourceIdentityVerifier:
    def verify(
        self,
        *,
        project_root: Path,
        migration_envelope_sha256: str,
        migration_envelope: dict[str, object],
        source: CheckoutRecord,
    ) -> SourceIdentityRecord:
        if source.role is not RepositoryRole.SOURCE:
            raise WorkflowError("source identity requires the frozen source checkout")
        root = (project_root.resolve() / source.checkout_path).resolve()
        entry = _relative_path(migration_envelope.get("source_driver_entry_or_repository_hint"))
        path = (root / entry).resolve()
        if root not in path.parents or not path.is_file():
            raise WorkflowError("frozen source entry is absent from its checkout root")
        blob = self._git(project_root, root, "rev-parse", f"{source.resolved_commit}:{entry}")
        data = self._git_bytes(project_root, root, "cat-file", "blob", blob)
        if data != path.read_bytes():
            raise WorkflowError("frozen source entry bytes differ from its Git blob")
        observed = ObservedSourceIdentity(
            RepositoryRole.SOURCE,
            source.resolved_commit,
            object_id(blob, "source identity blob"),
            hashlib.sha256(data).hexdigest(),
            len(data),
        )
        return SourceIdentityRecord(
            migration_envelope_sha256,
            source.checkout_path,
            entry,
            observed,
            SourceIdentityStatus.VERIFIED,
            (
                "source entry is tracked by the frozen source commit",
                "source worktree bytes equal the frozen Git blob",
            ),
            (),
            utc_now(),
        )

    @staticmethod
    def _git(root: Path, checkout: Path, *arguments: str) -> str:
        completed = subprocess.run(
            ("git", "-C", str(checkout), *arguments),
            cwd=root,
            check=False,
            capture_output=True,
            text=True,
        )
        if completed.returncode != 0:
            raise WorkflowError(
                f"source identity Git verification failed: {completed.stderr.strip()}"
            )
        return completed.stdout.strip()

    @staticmethod
    def _git_bytes(root: Path, checkout: Path, *arguments: str) -> bytes:
        completed = subprocess.run(
            ("git", "-C", str(checkout), *arguments),
            cwd=root,
            check=False,
            capture_output=True,
        )
        if completed.returncode != 0:
            stderr = completed.stderr.decode("utf-8", errors="replace")
            raise WorkflowError(f"source identity Git blob verification failed: {stderr.strip()}")
        return completed.stdout


def _relative_path(value: object) -> str:
    text = nonempty(value, "source identity entry")
    path = PurePosixPath(text)
    if path.is_absolute() or ".." in path.parts or path.as_posix() in {"", "."}:
        raise WorkflowError("source identity entry must be a safe relative path")
    return path.as_posix()


def validate_source_identity(
    record: SourceIdentityRecord,
    *,
    project_root: Path,
    migration_envelope_sha256: str,
    migration_envelope: dict[str, object],
    source: CheckoutRecord,
) -> None:
    observed = SourceIdentityVerifier().verify(
        project_root=project_root,
        migration_envelope_sha256=migration_envelope_sha256,
        migration_envelope=migration_envelope,
        source=source,
    )
    if (
        record.migration_envelope_sha256,
        record.source_root,
        record.source_entry,
        record.observed_identity,
        record.status,
        record.observations,
        record.conflicts,
    ) != (
        observed.migration_envelope_sha256,
        observed.source_root,
        observed.source_entry,
        observed.observed_identity,
        observed.status,
        observed.observations,
        observed.conflicts,
    ):
        raise WorkflowError("source identity record differs from the real envelope and checkout")
