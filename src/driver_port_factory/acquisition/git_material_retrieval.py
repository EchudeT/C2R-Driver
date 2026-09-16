from __future__ import annotations

import hashlib
from pathlib import Path

from ..core.execution import CommandRunner
from ..core.models import utc_now
from .facets import EvidenceFacet, RetrievalOutcome
from .locators import GitBlobLocator
from .material import GitBlobOrigin, MaterialRecord
from .material_content import MaterialContentPolicy
from .repository_manifest import RepositoryAcquisition
from .retrieval_result import RetrievalFailure, RetrievedMaterial


class GitMaterialRetriever:
    def __init__(self, project_root: Path, acquisition: RepositoryAcquisition) -> None:
        self.root = project_root.resolve()
        self.acquisition = acquisition
        self.git_runs = CommandRunner(self.root / ".dpf" / "command-runs" / "evidence-closure")

    def retrieve(
        self,
        facet: EvidenceFacet,
        locator: GitBlobLocator,
        material_id: str,
    ) -> RetrievedMaterial:
        checkout = self.acquisition.checkout(locator.repository)
        checkout_root = (self.root / checkout.checkout_path).resolve()
        path = (checkout_root / locator.path).resolve()
        if path != checkout_root and checkout_root not in path.parents:
            raise RetrievalFailure(RetrievalOutcome.FAILED, "Git locator escapes checkout")
        blob = (
            self._git(
                checkout_root,
                ("rev-parse", f"{checkout.resolved_commit}:{locator.path}"),
                missing=RetrievalOutcome.NOT_FOUND,
            )
            .decode("ascii", errors="strict")
            .strip()
        )
        if not path.is_file():
            raise RetrievalFailure(
                RetrievalOutcome.NOT_FOUND,
                f"tracked file is absent from {locator.repository.value}: {locator.path}",
            )
        repository_bytes = self._git(checkout_root, ("cat-file", "blob", blob))
        workspace_bytes = path.read_bytes()
        if repository_bytes != workspace_bytes:
            raise RetrievalFailure(
                RetrievalOutcome.CONFLICT,
                f"working file bytes differ from Git blob {blob}",
            )
        media_type = MaterialContentPolicy.media_type(path)
        MaterialContentPolicy.validate(workspace_bytes, media_type, path.name)
        policy = locator.policy
        record = MaterialRecord(
            material_id,
            facet,
            str(path.relative_to(self.root)),
            checkout.source_url,
            checkout.resolved_commit,
            utc_now(),
            policy.license_note,
            policy.redistribution,
            hashlib.sha256(workspace_bytes).hexdigest(),
            len(workspace_bytes),
            media_type,
            policy.original,
            MaterialContentPolicy.indexable(media_type, path),
            GitBlobOrigin(
                locator.repository,
                checkout.resolved_commit,
                blob,
                locator.path,
            ),
            policy.derived_from,
            policy.original_path,
            policy.page_map,
        )
        return RetrievedMaterial(
            record,
            f"verified {locator.repository.value} Git blob {blob}",
        )

    def _git(
        self,
        cwd: Path,
        arguments: tuple[str, ...],
        *,
        missing: RetrievalOutcome = RetrievalOutcome.FAILED,
    ) -> bytes:
        result = self.git_runs.run(("git", "-C", str(cwd), *arguments), cwd=self.root)
        stdout = Path(result.stdout_path).read_bytes()
        if result.exit_code != 0:
            stderr = Path(result.stderr_path).read_text(encoding="utf-8", errors="replace")
            raise RetrievalFailure(missing, stderr.strip() or "Git evidence lookup failed")
        return stdout
