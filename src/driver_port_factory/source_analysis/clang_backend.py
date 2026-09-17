from __future__ import annotations

import hashlib
import json
import os
import shutil
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from ..core.execution import CommandResult, CommandRunner
from ..core.models import WorkflowError
from ..knowledge.index import file_sha256
from .ast_projection import ClosureAstProjector, ClosureFileSet
from .compiler import AbiCompatibility, GccCompatibleCommand
from .fact_model import RawFactKind


class AnalyzerFamily(StrEnum):
    CLANG_LLVM = "clang-llvm"


class RawFactFormat(StrEnum):
    CLANG_AST_JSON = "clang-ast-json"
    CLANG_AST_CLOSURE_JSON = "clang-ast-closure-json"
    PREPROCESSED_C = "c-preprocessed-text-with-defines"
    CLANG_RECORD_LAYOUT = "clang-record-layout-dump"
    LLVM_IR_DEBUG = "llvm-ir-with-debug-metadata"
    CLANG_CFG = "clang-static-analyzer-cfg-dump"


class OutputStream(StrEnum):
    STDOUT = "stdout"
    COMBINED = "combined"


@dataclass(frozen=True, slots=True)
class ExtractionSpec:
    kind: RawFactKind
    arguments: tuple[str, ...]
    stream: OutputStream
    filename: str
    require_output: bool
    format: RawFactFormat


CLANG_EXTRACTIONS = (
    ExtractionSpec(
        RawFactKind.TYPED_AST,
        ("-Xclang", "-ast-dump=json", "-fsyntax-only"),
        OutputStream.STDOUT,
        "ast.json",
        True,
        RawFactFormat.CLANG_AST_JSON,
    ),
    ExtractionSpec(
        RawFactKind.PREPROCESSED_SOURCE,
        ("-E", "-dD"),
        OutputStream.STDOUT,
        "preprocessed.i",
        True,
        RawFactFormat.PREPROCESSED_C,
    ),
    ExtractionSpec(
        RawFactKind.RECORD_LAYOUT,
        ("-Xclang", "-fdump-record-layouts-complete", "-fsyntax-only"),
        OutputStream.COMBINED,
        "record-layout.txt",
        False,
        RawFactFormat.CLANG_RECORD_LAYOUT,
    ),
    ExtractionSpec(
        RawFactKind.LLVM_IR,
        ("-S", "-emit-llvm", "-g", "-O0", "-o", "-"),
        OutputStream.STDOUT,
        "module.ll",
        True,
        RawFactFormat.LLVM_IR_DEBUG,
    ),
    ExtractionSpec(
        RawFactKind.CFG,
        (
            "-Xclang",
            "-analyze",
            "-Xclang",
            "-analyzer-checker=debug.DumpCFG",
            "-fsyntax-only",
        ),
        OutputStream.COMBINED,
        "cfg.txt",
        False,
        RawFactFormat.CLANG_CFG,
    ),
)


@dataclass(frozen=True, slots=True)
class AnalyzerIdentity:
    family: AnalyzerFamily
    requested_executable: str
    resolved_path: Path
    sha256: str
    version_output: str

    def to_record(
        self,
        *,
        requested_target_triple: str,
        observed_target_triple: str,
        target_abi: dict[str, object],
    ) -> dict[str, Any]:
        return {
            "family": self.family,
            "requested_executable": self.requested_executable,
            "resolved_path": str(self.resolved_path),
            "sha256": self.sha256,
            "version_output": self.version_output,
            "version_output_sha256": hashlib.sha256(self.version_output.encode()).hexdigest(),
            "requested_target_triple": requested_target_triple,
            "observed_target_triple": observed_target_triple,
            "target_abi": target_abi,
        }


@dataclass(frozen=True, slots=True)
class UnitExtraction:
    raw_records: dict[RawFactKind, dict[str, Any]]
    raw_paths: tuple[Path, ...]
    typed_ast: dict[str, Any]
    target_triple: str
    target_abi: dict[str, object]


class ClangAnalysisBackend:
    """Own Clang identity, command construction, execution, and raw artifact capture."""

    family = AnalyzerFamily.CLANG_LLVM

    def __init__(self, identity: AnalyzerIdentity) -> None:
        self.identity = identity

    @classmethod
    def discover(cls, executable_name: str) -> ClangAnalysisBackend:
        executable = shutil.which(executable_name)
        if not executable:
            raise WorkflowError(f"structured analyzer is unavailable: {executable_name}")
        invoked = Path(executable)
        resolved = invoked.resolve()
        return cls(
            AnalyzerIdentity(
                family=cls.family,
                requested_executable=executable_name,
                resolved_path=resolved,
                sha256=file_sha256(resolved),
                version_output=GccCompatibleCommand.version(invoked),
            )
        )

    def extract(
        self,
        *,
        project_root: Path,
        unit_dir: Path,
        unit_id: str,
        source_path: Path,
        compile_directory: Path,
        arguments: list[str],
        target_triple: str,
        expected_abi: dict[str, object],
        closure_files: ClosureFileSet,
    ) -> UnitExtraction:
        observed_target = GccCompatibleCommand.effective_target_triple(
            arguments,
            compile_directory,
            executable=self.identity.resolved_path,
            target_triple=target_triple,
        )
        observed_abi = GccCompatibleCommand.abi_signature(
            arguments,
            compile_directory,
            executable=self.identity.resolved_path,
            target_triple=target_triple,
        )
        if observed_target != observed_abi["target_triple"]:
            raise WorkflowError("analyzer target probes disagree")
        if AbiCompatibility.from_record(observed_abi) != AbiCompatibility.from_record(expected_abi):
            raise WorkflowError("analyzer target ABI differs from frozen source compiler")

        raw_dir = unit_dir / "raw"
        raw_dir.mkdir(parents=True)
        runner = CommandRunner(unit_dir / "runs")
        base_arguments = GccCompatibleCommand.analysis_base_arguments(
            arguments,
            self.identity.resolved_path,
            target_triple=target_triple,
        )
        raw_records: dict[RawFactKind, dict[str, Any]] = {}
        raw_paths: list[Path] = []
        typed_ast: dict[str, Any] | None = None

        for spec in CLANG_EXTRACTIONS:
            result = runner.run(
                [*base_arguments, *spec.arguments], cwd=compile_directory, timeout_seconds=120
            )
            self._require_success(spec, result)
            raw_path = raw_dir / spec.filename
            capture_path, capture_sha256, capture_size = self._capture(
                result,
                spec.stream,
                raw_path if spec.stream is OutputStream.COMBINED else None,
            )
            if spec.require_output and capture_size == 0:
                raise WorkflowError(f"{spec.kind.value} extraction was empty")
            if spec.kind is RawFactKind.TYPED_AST:
                typed_ast = ClosureAstProjector(closure_files).project(
                    capture_path,
                    raw_path,
                    compile_directory=compile_directory,
                    capture_sha256=capture_sha256,
                    capture_size=capture_size,
                )
                raw_format = RawFactFormat.CLANG_AST_CLOSURE_JSON
            else:
                if capture_path != raw_path:
                    self._link_or_copy(capture_path, raw_path)
                raw_format = spec.format
            raw_paths.append(raw_path)
            raw_records[spec.kind] = {
                "format": raw_format,
                "path": str(raw_path.relative_to(project_root)),
                "sha256": file_sha256(raw_path),
                "size": raw_path.stat().st_size,
                "capture_path": str(capture_path.relative_to(project_root)),
                "capture_sha256": capture_sha256,
                "capture_size": capture_size,
            }

        if typed_ast is None:
            raise WorkflowError("typed AST was not captured")
        return UnitExtraction(
            raw_records=raw_records,
            raw_paths=tuple(raw_paths),
            typed_ast=typed_ast,
            target_triple=observed_target,
            target_abi=observed_abi,
        )

    @staticmethod
    def _require_success(spec: ExtractionSpec, result: CommandResult) -> None:
        if result.launched and not result.timed_out and result.exit_code == 0:
            return
        diagnostic = Path(result.stderr_path).read_text(encoding="utf-8", errors="replace")
        raise WorkflowError(
            f"{spec.kind.value} extraction failed: "
            + (diagnostic.strip()[:2000] or f"exit {result.exit_code}")
        )

    def _capture(
        self,
        result: CommandResult,
        stream: OutputStream,
        combined_path: Path | None,
    ) -> tuple[Path, str, int]:
        stdout = Path(result.stdout_path)
        if stream is OutputStream.STDOUT:
            return stdout, result.stdout_sha256, stdout.stat().st_size
        if stream is not OutputStream.COMBINED or combined_path is None:
            raise WorkflowError(f"unsupported command output stream: {stream.value}")
        stderr = Path(result.stderr_path)
        with combined_path.open("wb") as output:
            self._copy(stdout, output)
            if stdout.stat().st_size and stderr.stat().st_size:
                output.write(b"\n")
            self._copy(stderr, output)
        return combined_path, file_sha256(combined_path), combined_path.stat().st_size

    @staticmethod
    def _copy(source: Path, output: Any) -> None:
        with source.open("rb") as stream:
            shutil.copyfileobj(stream, output, length=1024 * 1024)

    @staticmethod
    def _link_or_copy(source: Path, destination: Path) -> None:
        try:
            os.link(source, destination)
        except OSError:
            shutil.copyfile(source, destination)

    @staticmethod
    def _json_bytes(value: list[dict[str, Any]]) -> bytes:
        return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()
