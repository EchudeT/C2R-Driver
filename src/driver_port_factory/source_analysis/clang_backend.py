from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import asdict, dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from ..core.execution import CommandResult, CommandRunner
from ..core.models import WorkflowError
from ..knowledge.index import file_sha256
from .compiler import GccCompatibleCommand
from .fact_model import RawFactKind


class AnalyzerFamily(StrEnum):
    CLANG_LLVM = "clang-llvm"


class RawFactFormat(StrEnum):
    CLANG_AST_JSON = "clang-ast-json"
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
    command_records_path: Path
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
        if observed_abi != expected_abi:
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
        command_records: list[dict[str, Any]] = []
        commands_path = unit_dir / "commands.json"
        typed_ast: dict[str, Any] | None = None

        for spec in CLANG_EXTRACTIONS:
            result = runner.run(
                [*base_arguments, *spec.arguments], cwd=compile_directory, timeout_seconds=120
            )
            output = self._select_output(result, spec.stream)
            command_records.append(
                {
                    "schema_version": 1,
                    "unit_id": unit_id,
                    "source_path": str(source_path.resolve()),
                    "fact_kind": spec.kind,
                    "raw_format": spec.format,
                    "output_stream": spec.stream,
                    "output_sha256": hashlib.sha256(output).hexdigest(),
                    "output_size": len(output),
                    "analyzer_sha256": self.identity.sha256,
                    "target_triple": observed_target,
                    **asdict(result),
                }
            )
            commands_path.write_bytes(self._json_bytes(command_records))
            self._require_success(spec, result)
            if spec.require_output and not output:
                raise WorkflowError(f"{spec.kind.value} extraction was empty")
            raw_path = raw_dir / spec.filename
            raw_path.write_bytes(output)
            raw_paths.append(raw_path)
            raw_records[spec.kind] = {
                "format": spec.format,
                "path": str(raw_path.relative_to(project_root)),
                "sha256": file_sha256(raw_path),
                "size": len(output),
            }
            if spec.kind is RawFactKind.TYPED_AST:
                typed_ast = self._json_object(output)

        if typed_ast is None:
            raise WorkflowError("typed AST was not captured")
        return UnitExtraction(
            raw_records=raw_records,
            raw_paths=tuple(raw_paths),
            command_records_path=commands_path,
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

    @staticmethod
    def _select_output(result: CommandResult, stream: OutputStream) -> bytes:
        stdout = Path(result.stdout_path).read_bytes()
        stderr = Path(result.stderr_path).read_bytes()
        if stream is OutputStream.STDOUT:
            return stdout
        if stream is OutputStream.COMBINED:
            return stdout + (b"\n" if stdout and stderr else b"") + stderr
        raise WorkflowError(f"unsupported command output stream: {stream.value}")

    @staticmethod
    def _json_object(data: bytes) -> dict[str, Any]:
        try:
            value = json.loads(data)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise WorkflowError("typed AST is not UTF-8 JSON") from error
        if not isinstance(value, dict):
            raise WorkflowError("typed AST is not a JSON object")
        return value

    @staticmethod
    def _json_bytes(value: list[dict[str, Any]]) -> bytes:
        return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()
