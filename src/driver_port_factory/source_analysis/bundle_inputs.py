from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

from ..core.models import WorkflowError
from ..knowledge.index import file_sha256
from .bundle_artifacts import ArtifactPayload, require_within


class FrozenAnalysisInputs:
    """Prove that structured analysis used the frozen closure and analyzer identities."""

    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root

    def validate(
        self,
        facts: dict[str, Any],
        compile_manifest: dict[str, Any],
        compilation_database: list[object],
        compile_payload: ArtifactPayload,
        database_payload: ArtifactPayload,
    ) -> Path:
        self._validate_document_links(facts, compile_manifest, compile_payload, database_payload)
        self._validate_canonical_database(compilation_database, database_payload)
        source_root = Path(str(compile_manifest.get("source_root", ""))).resolve()
        require_within(self.project_root, source_root, "source root")
        if facts.get("source_root") != str(source_root):
            raise WorkflowError("structured facts source root differs from the compile manifest")
        identity = self._git_identity(source_root)
        if facts.get("source_checkout_identity") != identity:
            raise WorkflowError("structured facts source checkout identity is stale or forged")
        if facts.get("source_revision") != compile_manifest.get("source_revision"):
            raise WorkflowError("structured facts source revision differs from compile manifest")
        if identity["revision"] != compile_manifest.get("source_revision"):
            raise WorkflowError("source checkout revision changed after source closure")
        self._validate_analyzer(facts, compile_manifest)
        return source_root

    @staticmethod
    def _validate_document_links(
        facts: dict[str, Any],
        compile_manifest: dict[str, Any],
        compile_payload: ArtifactPayload,
        database_payload: ArtifactPayload,
    ) -> None:
        if facts.get("compile_manifest_sha256") != compile_payload.ref.digest:
            raise WorkflowError("structured facts reference a different compile manifest")
        if facts.get("compilation_database_sha256") != database_payload.ref.digest:
            raise WorkflowError("structured facts reference a different compilation database")
        if compile_manifest.get("compilation_database_sha256") != database_payload.ref.digest:
            raise WorkflowError("compile manifest references a different compilation database")

    @staticmethod
    def _validate_canonical_database(
        compilation_database: list[object], database_payload: ArtifactPayload
    ) -> None:
        canonical = (
            json.dumps(
                compilation_database,
                ensure_ascii=False,
                sort_keys=True,
                indent=2,
            )
            + "\n"
        ).encode()
        if hashlib.sha256(canonical).hexdigest() != database_payload.ref.digest:
            raise WorkflowError("compilation database bytes are not canonical or digest-bound")

    @staticmethod
    def _validate_analyzer(facts: dict[str, Any], compile_manifest: dict[str, Any]) -> None:
        analyzer = facts.get("analyzer")
        compiler = compile_manifest.get("compiler")
        if not isinstance(analyzer, dict) or not isinstance(compiler, dict):
            raise WorkflowError("structured facts lack analyzer/compiler identities")
        resolved = Path(str(compiler.get("resolved_path", ""))).resolve()
        if not resolved.is_file() or file_sha256(resolved) != compiler.get("sha256"):
            raise WorkflowError("frozen analyzer executable identity changed")
        expected = {
            "resolved_path": str(resolved),
            "sha256": compiler.get("sha256"),
            "requested_target_triple": compiler.get("verified_target_triple"),
            "observed_target_triple": compiler.get("verified_target_triple"),
            "target_abi": compiler.get("verified_target_abi"),
        }
        mismatched = [
            field
            for field, expected_value in expected.items()
            if analyzer.get(field) != expected_value
        ]
        if mismatched:
            raise WorkflowError(
                "structured analyzer differs from the frozen compiler: "
                + ", ".join(sorted(mismatched))
            )
        version_output = analyzer.get("version_output")
        if not isinstance(version_output, str) or hashlib.sha256(
            version_output.encode()
        ).hexdigest() != analyzer.get("version_output_sha256"):
            raise WorkflowError("structured analyzer version identity is internally inconsistent")

    @staticmethod
    def _git_identity(source_root: Path) -> dict[str, Any]:
        def git(*arguments: str) -> str:
            completed = subprocess.run(
                ["git", "-C", str(source_root), *arguments],
                check=False,
                capture_output=True,
                text=True,
                timeout=30,
            )
            if completed.returncode != 0:
                raise WorkflowError(
                    "cannot verify frozen source checkout: "
                    + (completed.stderr.strip() or "git command failed")
                )
            return completed.stdout.strip()

        return {
            "revision": git("rev-parse", "HEAD^{commit}").lower(),
            "tree_id": git("rev-parse", "HEAD^{tree}").lower(),
            "clean": not git("status", "--porcelain=v1", "--untracked-files=all"),
        }
