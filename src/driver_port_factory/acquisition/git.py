from __future__ import annotations

import hashlib
import re
from dataclasses import asdict
from pathlib import Path

from ..core.execution import CommandResult, CommandRunner
from ..core.models import WorkflowError, utc_now
from ..core.store import canonical_json
from .models import CheckoutRecord, RepositoryRole, RepositorySpec

_OBJECT_ID = re.compile(r"^[0-9a-fA-F]{40}(?:[0-9a-fA-F]{24})?$")


class GitAcquirer:
    """Read-only remote Git acquisition into task-local bare repositories/worktrees."""

    def __init__(self, project_root: Path, control_root: Path) -> None:
        self.project_root = project_root.resolve()
        self.control_root = control_root.resolve()
        self.runner = CommandRunner(self.control_root / "command-runs" / "acquisition")
        self.command_results: list[CommandResult] = []

    def _git(self, arguments: list[str], *, cwd: Path) -> str:
        result = self.runner.run(["git", *arguments], cwd=cwd, timeout_seconds=600)
        self.command_results.append(result)
        stdout = Path(result.stdout_path).read_text(encoding="utf-8", errors="replace")
        if result.exit_code != 0:
            stderr = Path(result.stderr_path).read_text(encoding="utf-8", errors="replace")
            raise WorkflowError(
                f"git command failed ({result.exit_code}): git {' '.join(arguments)}: "
                f"{stderr.strip()}"
            )
        return stdout.strip()

    def resolve_ref(
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
            commit = self._git(
                ["-C", str(local.resolve()), "rev-parse", f"{requested_ref}^{{commit}}"],
                cwd=self.project_root,
            )
        elif _OBJECT_ID.fullmatch(requested_ref):
            commit = requested_ref.lower()
        else:
            patterns = [
                requested_ref,
                f"refs/heads/{requested_ref}",
                f"refs/tags/{requested_ref}",
                f"refs/tags/{requested_ref}^{{}}",
            ]
            output = self._git(["ls-remote", "--exit-code", url, *patterns], cwd=self.project_root)
            rows = [line.split(maxsplit=1) for line in output.splitlines() if line.strip()]
            peeled = [row[0] for row in rows if row[1].endswith("^{}")]
            heads = [row[0] for row in rows if row[1] == f"refs/heads/{requested_ref}"]
            direct = [row[0] for row in rows]
            choices = peeled or heads or direct
            if not choices:
                raise WorkflowError(f"cannot resolve {requested_ref!r} from {url}")
            if len(set(choices)) != 1:
                raise WorkflowError(f"ref {requested_ref!r} resolves to multiple commits at {url}")
            commit = choices[0]
        if not _OBJECT_ID.fullmatch(commit):
            raise WorkflowError(f"resolved Git commit is invalid: {commit}")
        return RepositorySpec(
            role=role,
            platform=platform,
            url=url,
            requested_ref=requested_ref,
            resolved_commit=commit.lower(),
            selection_rule=selection_rule,
        )

    def acquire(self, spec: RepositorySpec, checkout_name: str) -> CheckoutRecord:
        repositories = self.control_root / "git"
        checkouts = self.control_root / "worktrees"
        repositories.mkdir(parents=True, exist_ok=True)
        checkouts.mkdir(parents=True, exist_ok=True)
        bare = repositories / f"{spec.role.value}.git"
        checkout = checkouts / checkout_name
        if bare.exists() or checkout.exists():
            raise WorkflowError(
                f"refusing to overwrite existing acquisition paths: {bare}, {checkout}"
            )
        self._git(["init", "--bare", str(bare)], cwd=self.project_root)
        self._git(
            ["-C", str(bare), "remote", "add", "origin", spec.url],
            cwd=self.project_root,
        )
        self._git(
            [
                "-C",
                str(bare),
                "fetch",
                "--depth=1",
                "--no-tags",
                "origin",
                spec.requested_ref,
            ],
            cwd=self.project_root,
        )
        fetched_commit = self._git(
            ["-C", str(bare), "rev-parse", "FETCH_HEAD^{commit}"],
            cwd=self.project_root,
        ).lower()
        if fetched_commit != spec.resolved_commit:
            raise WorkflowError(
                f"fetched {spec.role.value} commit {fetched_commit} does not match "
                f"planned {spec.resolved_commit}"
            )
        tree_id = self._git(
            ["-C", str(bare), "rev-parse", f"{fetched_commit}^{{tree}}"],
            cwd=self.project_root,
        ).lower()
        self._git(
            ["-C", str(bare), "worktree", "add", "--detach", str(checkout), fetched_commit],
            cwd=self.project_root,
        )
        clean = not self._git(
            ["-C", str(checkout), "status", "--porcelain", "--untracked-files=no"],
            cwd=self.project_root,
        )
        acquired_at = utc_now()
        lock = {
            "role": spec.role.value,
            "platform": spec.platform,
            "source_url": spec.url,
            "resolved_commit": fetched_commit,
            "tree_id": tree_id,
        }
        lock_sha256 = hashlib.sha256(canonical_json(lock).encode("utf-8")).hexdigest()
        return CheckoutRecord(
            role=spec.role,
            platform=spec.platform,
            source_url=spec.url,
            requested_ref=spec.requested_ref,
            resolved_commit=fetched_commit,
            tree_id=tree_id,
            bare_repository=str(bare.relative_to(self.project_root)),
            checkout_path=str(checkout.relative_to(self.project_root)),
            clean=clean,
            lock_sha256=lock_sha256,
            acquired_at=acquired_at,
        )

    def create_target_worktree(self, spec: RepositorySpec, project_id: str) -> str:
        bare = self.control_root / "git" / f"{spec.role.value}.git"
        target = self.control_root / "worktrees" / "target-working"
        if spec.role is not RepositoryRole.TARGET:
            raise WorkflowError("a writable worktree can only be created for the target repository")
        safe_id = re.sub(r"[^A-Za-z0-9._-]+", "-", project_id).strip("-") or "run"
        branch = f"dpf/{safe_id}"
        self._git(
            [
                "-C",
                str(bare),
                "worktree",
                "add",
                "-b",
                branch,
                str(target),
                spec.resolved_commit,
            ],
            cwd=self.project_root,
        )
        return str(target.relative_to(self.project_root))

    def verify(self, record: CheckoutRecord) -> dict[str, object]:
        checkout = self.project_root / record.checkout_path
        bare = self.project_root / record.bare_repository
        observed_commit = self._git(
            ["-C", str(checkout), "rev-parse", "HEAD^{commit}"], cwd=self.project_root
        ).lower()
        observed_tree = self._git(
            ["-C", str(checkout), "rev-parse", "HEAD^{tree}"], cwd=self.project_root
        ).lower()
        observed_url = self._git(
            ["-C", str(bare), "remote", "get-url", "origin"], cwd=self.project_root
        )
        clean = not self._git(
            ["-C", str(checkout), "status", "--porcelain", "--untracked-files=no"],
            cwd=self.project_root,
        )
        checks = {
            "commit": observed_commit == record.resolved_commit,
            "tree": observed_tree == record.tree_id,
            "origin": observed_url == record.source_url,
            "clean": clean,
        }
        return {"role": record.role.value, "checks": checks, "valid": all(checks.values())}

    def serialized_commands(self) -> list[dict[str, object]]:
        return [asdict(result) for result in self.command_results]
