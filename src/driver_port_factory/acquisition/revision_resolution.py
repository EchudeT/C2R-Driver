from __future__ import annotations

import re
import tempfile
from pathlib import Path

from ..core.models import WorkflowError
from .commands import RepositoryCommandKind, RepositoryCommandRecord
from .git_execution import RepositoryGit
from .repository_role import RepositoryRole
from .repository_spec import RepositorySpec

_OBJECT_ID = re.compile(r"^[0-9a-fA-F]{40}(?:[0-9a-fA-F]{24})?$")


class RevisionResolver:
    """Resolve a requested revision and actively prove remote full-commit membership."""

    def __init__(self, project_root: Path, control_root: Path) -> None:
        self.project_root = project_root.resolve()
        self.control_root = control_root.resolve()
        self.git = RepositoryGit(self.project_root, self.control_root)

    def resolve(
        self,
        *,
        role: RepositoryRole,
        platform: str,
        url: str,
        requested_ref: str,
        selection_rule: str,
    ) -> RepositorySpec:
        if not url or not requested_ref:
            raise WorkflowError(f"{role.value} URL and ref must be non-empty")
        local = Path(url).expanduser()
        if local.exists():
            commit = self.git.run(
                ["-C", str(local.resolve()), "rev-parse", f"{requested_ref}^{{commit}}"],
                operation=RepositoryCommandKind.REVISION_RESOLUTION,
                role=role,
            ).stdout
        elif _OBJECT_ID.fullmatch(requested_ref):
            commit = self._prove_remote_commit(role, url, requested_ref.lower())
        else:
            commit = self._resolve_remote_name(role, url, requested_ref)
        if not _OBJECT_ID.fullmatch(commit):
            raise WorkflowError(f"resolved Git commit is invalid: {commit}")
        return RepositorySpec(
            role,
            platform,
            url,
            requested_ref,
            commit.lower(),
            selection_rule,
        )

    def _resolve_remote_name(self, role: RepositoryRole, url: str, requested_ref: str) -> str:
        patterns = [
            requested_ref,
            f"refs/heads/{requested_ref}",
            f"refs/tags/{requested_ref}",
            f"refs/tags/{requested_ref}^{{}}",
        ]
        output = self.git.run(
            ["ls-remote", "--exit-code", url, *patterns],
            operation=RepositoryCommandKind.REVISION_RESOLUTION,
            role=role,
        ).stdout
        rows = [line.split(maxsplit=1) for line in output.splitlines() if line.strip()]
        peeled = [row[0] for row in rows if row[1].endswith("^{}")]
        heads = [row[0] for row in rows if row[1] == f"refs/heads/{requested_ref}"]
        direct = [row[0] for row in rows]
        choices = peeled or heads or direct
        if not choices:
            raise WorkflowError(f"cannot resolve {requested_ref!r} from {url}")
        if len(set(choices)) != 1:
            raise WorkflowError(f"ref {requested_ref!r} resolves to multiple commits at {url}")
        return choices[0]

    def _prove_remote_commit(self, role: RepositoryRole, url: str, commit: str) -> str:
        attempts = self.control_root / "revision-probes"
        attempts.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=f"{role.value}-", dir=attempts) as temporary:
            probe = Path(temporary)
            self.git.run(
                ["init", "--bare", str(probe)],
                operation=RepositoryCommandKind.BASELINE_INITIALIZATION,
                role=role,
            )
            self.git.run(
                ["-C", str(probe), "remote", "add", "origin", url],
                operation=RepositoryCommandKind.ORIGIN_CONFIGURATION,
                role=role,
            )
            self.git.run(
                ["-C", str(probe), "fetch", "--depth=1", "--no-tags", "origin", commit],
                operation=RepositoryCommandKind.COMMIT_MEMBERSHIP,
                role=role,
            )
            resolved = self.git.run(
                ["-C", str(probe), "rev-parse", "FETCH_HEAD^{commit}"],
                operation=RepositoryCommandKind.REVISION_RESOLUTION,
                role=role,
            ).stdout.lower()
        if resolved != commit:
            raise WorkflowError(f"remote {role.value} origin did not return the claimed commit")
        return resolved

    @property
    def commands(self) -> tuple[RepositoryCommandRecord, ...]:
        return self.git.records
