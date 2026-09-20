from __future__ import annotations

import hashlib
import json
import subprocess
from collections import Counter
from pathlib import Path
from typing import Any

from ..core.models import WorkflowError
from ..core.project import Project
from ..knowledge.index import file_sha256
from .ast_projection import ClosureFileSet
from .clang_backend import AnalyzerFamily, ClangAnalysisBackend
from .compiler import CompilerFamily, compiler_adapter
from .contracts import SourceAnalysisArtifact
from .fact_model import (
    FactAvailability,
    RawFactKind,
    SemanticFactDomain,
    StructuredAnalysisStatus,
)
from .function_pointers import ClosureFunctionPointerResolver
from .unit_extraction import TranslationUnitExtractor, UnitResult


class StructuredBundleExtractor:
    """Bind frozen closure inputs and aggregate complete structured-C unit evidence."""

    def extract(
        self,
        *,
        project: Project,
        attempt_dir: Path,
        compile_manifest: dict[str, Any],
        compilation_database: list[dict[str, Any]],
        compile_manifest_digest: str,
        compilation_database_digest: str,
        analyzer_name: str,
        analyzer_family: AnalyzerFamily,
    ) -> tuple[dict[str, Any], tuple[tuple[SourceAnalysisArtifact, Path], ...]]:
        if analyzer_family is not AnalyzerFamily.CLANG_LLVM:
            raise WorkflowError(f"unsupported structured analyzer family: {analyzer_family.value}")
        compiler_record = compile_manifest.get("compiler")
        if not isinstance(compiler_record, dict):
            raise WorkflowError("compile manifest has no compiler identity")
        command_adapter = self._command_adapter(compiler_record)
        self._validate_database(compilation_database, compile_manifest)
        target_triple, target_abi = self._target(compiler_record)
        backend = ClangAnalysisBackend.discover(analyzer_name)

        source_root = Path(str(compile_manifest.get("source_root", ""))).resolve()
        if not source_root.is_dir():
            raise WorkflowError("frozen source root no longer exists")
        identity_before = self._source_identity(source_root)
        if identity_before["revision"] != compile_manifest.get("source_revision"):
            raise WorkflowError("source checkout revision differs from compile manifest")
        if not identity_before["clean"]:
            raise WorkflowError("source checkout is dirty before structured analysis")

        units = compile_manifest.get("translation_units")
        if not isinstance(units, list) or not units:
            raise WorkflowError("compile manifest has no translation units")
        unit_extractor = TranslationUnitExtractor(
            project=project,
            attempt_dir=attempt_dir,
            source_root=source_root,
            database_by_file=self._index_database(compilation_database),
            backend=backend,
            target_triple=target_triple,
            target_abi=target_abi,
            command_adapter=command_adapter,
            closure_files=ClosureFileSet.from_manifest(compile_manifest),
        )
        unit_results = [unit_extractor.extract(unit) for unit in units]
        self._finalize_semantics(project, unit_results)
        self._validate_fact_coverage(unit_results)
        analyzer_targets = {result.fact["analyzer_target_triple"] for result in unit_results}
        if len(analyzer_targets) != 1:
            raise WorkflowError("translation units do not share one analyzer target triple")
        analyzer_abis = {
            json.dumps(result.fact["verified_target_abi"], sort_keys=True)
            for result in unit_results
        }
        if len(analyzer_abis) != 1:
            raise WorkflowError("translation units do not share one analyzer target ABI")
        identity_after = self._source_identity(source_root)
        if identity_after != identity_before:
            raise WorkflowError("structured analyzer changed the frozen source checkout")
        facts = self._facts(
            compile_manifest,
            compile_manifest_digest,
            compilation_database_digest,
            source_root,
            identity_after,
            backend,
            target_triple,
            unit_results[0].fact["verified_target_abi"],
            analyzer_targets.pop(),
            unit_results,
        )
        return facts, self._artifact_paths(unit_results)

    @staticmethod
    def _finalize_semantics(project: Project, unit_results: list[UnitResult]) -> None:
        ClosureFunctionPointerResolver.resolve([result.semantic_index for result in unit_results])
        for result in unit_results:
            data = (
                json.dumps(
                    result.semantic_index,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                + "\n"
            ).encode()
            project.validators.validate(
                SourceAnalysisArtifact.STRUCTURED_C_SEMANTIC_INDEX,
                data,
            )
            result.semantic_path.write_bytes(data)
            result.fact["semantic_index"]["sha256"] = file_sha256(result.semantic_path)
            result.fact["semantic_counts"] = result.semantic_index["counts"]

    @staticmethod
    def _command_adapter(compiler_record: dict[str, Any]) -> type:
        try:
            return compiler_adapter(CompilerFamily(compiler_record["family"]))
        except (KeyError, TypeError, ValueError) as error:
            raise WorkflowError("compile manifest has an unsupported compiler family") from error

    @staticmethod
    def _validate_database(
        compilation_database: list[dict[str, Any]], compile_manifest: dict[str, Any]
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
        if hashlib.sha256(canonical).hexdigest() != compile_manifest.get(
            "compilation_database_sha256"
        ):
            raise WorkflowError("compilation database differs from the frozen compile manifest")

    @staticmethod
    def _target(compiler_record: dict[str, Any]) -> tuple[str, dict[str, object]]:
        target_triple = compiler_record.get("verified_target_triple")
        target_abi = compiler_record.get("verified_target_abi")
        if not isinstance(target_triple, str) or not isinstance(target_abi, dict):
            raise WorkflowError("compile manifest has no verified target triple and ABI")
        return target_triple, target_abi

    @staticmethod
    def _validate_fact_coverage(unit_results: list[UnitResult]) -> None:
        extracted = {
            RawFactKind(kind) for result in unit_results for kind in result.fact["raw_facts"]
        }
        missing = set(RawFactKind) - extracted
        if missing:
            raise WorkflowError(
                "structured analysis omitted raw fact domains: "
                + ", ".join(sorted(kind.value for kind in missing))
            )

    def _facts(
        self,
        compile_manifest: dict[str, Any],
        compile_manifest_digest: str,
        compilation_database_digest: str,
        source_root: Path,
        source_identity: dict[str, Any],
        backend: ClangAnalysisBackend,
        target_triple: str,
        target_abi: dict[str, object],
        observed_target: str,
        unit_results: list[UnitResult],
    ) -> dict[str, Any]:
        aggregate_counts: Counter[str] = Counter()
        for result in unit_results:
            aggregate_counts.update(result.fact["semantic_counts"])
        return {
            "schema_version": 1,
            "status": StructuredAnalysisStatus.READY,
            "source_revision": compile_manifest.get("source_revision"),
            "source_root": str(source_root),
            "source_checkout_identity": source_identity,
            "compile_manifest_sha256": compile_manifest_digest,
            "compilation_database_sha256": compilation_database_digest,
            "analyzer": backend.identity.to_record(
                requested_target_triple=target_triple,
                observed_target_triple=observed_target,
                target_abi=target_abi,
            ),
            "fact_domains": self._fact_domain_status(unit_results),
            "semantic_counts": dict(sorted(aggregate_counts.items())),
            "units": [result.fact for result in unit_results],
            "unresolved_fact_domains": [],
            "semantic_policy": (
                "Facts come from Clang AST/CFG/layout/preprocessor and LLVM IR. Names are used "
                "only for identity correlation, never to infer driver behavior. Hardware-effect "
                "meaning remains unclassified until migration-contract mapping."
            ),
            "semantic_limits": [
                (
                    "Indirect calls are resolved only when all function-pointer alternatives are "
                    "proven exact and the holder is not mutable, aliased, or escaped."
                ),
                (
                    "Structural effect candidates are not classified as MMIO, PIO, DMA, IRQ, "
                    "locking, ownership, or recovery behavior without source/hardware evidence."
                ),
                (
                    "LLVM IR may omit unused inline definitions. Clang CFG deliberately skips "
                    "__inline-prefixed functions; unavailable_functions names these gaps. Typed "
                    "AST bodies remain available. READY means evidence extracted, not that "
                    "every semantic obligation is resolved. Migration and independent review "
                    "must inspect relevant gaps and unresolved indirect calls."
                ),
            ],
        }

    @staticmethod
    def _artifact_paths(
        unit_results: list[UnitResult],
    ) -> tuple[tuple[SourceAnalysisArtifact, Path], ...]:
        return tuple(
            (kind, path)
            for result in unit_results
            for kind, paths in (
                (SourceAnalysisArtifact.STRUCTURED_C_RAW_FACT, result.raw_paths),
                (
                    SourceAnalysisArtifact.STRUCTURED_C_SEMANTIC_INDEX,
                    (result.semantic_path,),
                ),
            )
            for path in paths
        )

    @staticmethod
    def _index_database(
        compilation_database: list[dict[str, Any]],
    ) -> dict[Path, dict[str, Any]]:
        result: dict[Path, dict[str, Any]] = {}
        for entry in compilation_database:
            if not isinstance(entry.get("file"), str):
                raise WorkflowError("compilation database contains an invalid entry")
            path = Path(entry["file"]).resolve()
            if path in result:
                raise WorkflowError(f"duplicate compilation database file: {path}")
            result[path] = entry
        return result

    @staticmethod
    def _fact_domain_status(
        unit_results: list[UnitResult],
    ) -> dict[SemanticFactDomain, FactAvailability]:
        def raw_status(kind: RawFactKind) -> FactAvailability:
            statuses = {
                FactAvailability(result.fact["raw_facts"][kind]["availability"])
                for result in unit_results
            }
            if FactAvailability.STRUCTURED in statuses:
                return FactAvailability.STRUCTURED
            if FactAvailability.RAW_VALIDATED in statuses:
                return FactAvailability.RAW_VALIDATED
            return FactAvailability.EMPTY_VALID

        return {
            SemanticFactDomain.TYPED_AST: FactAvailability.STRUCTURED,
            SemanticFactDomain.CODE_PROPERTY_GRAPH: FactAvailability.STRUCTURED,
            SemanticFactDomain.CFG: raw_status(RawFactKind.CFG),
            SemanticFactDomain.RECORD_LAYOUT: raw_status(RawFactKind.RECORD_LAYOUT),
            SemanticFactDomain.PREPROCESSOR: raw_status(RawFactKind.PREPROCESSED_SOURCE),
            SemanticFactDomain.LLVM_IR: raw_status(RawFactKind.LLVM_IR),
            SemanticFactDomain.CALLS: FactAvailability.STRUCTURED,
            SemanticFactDomain.GLOBALS: FactAvailability.STRUCTURED,
            SemanticFactDomain.EFFECTS: FactAvailability.STRUCTURED,
            SemanticFactDomain.SOURCE_SPANS: FactAvailability.STRUCTURED,
        }

    @staticmethod
    def _source_identity(source_root: Path) -> dict[str, Any]:
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
