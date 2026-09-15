from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

from ..core.models import WorkflowError
from .ast_index import AstSemanticIndexer
from .bundle_artifacts import ArtifactPayload, StructuredArtifactInventory, require_within
from .clang_backend import CLANG_EXTRACTIONS, ExtractionSpec, OutputStream
from .compiler import GccCompatibleCommand
from .fact_model import RawFactKind


class CommandEvidenceValidator:
    """Reconstruct analyzer commands and bind their captured streams to raw facts."""

    def __init__(self, inventory: StructuredArtifactInventory) -> None:
        self.inventory = inventory

    def validate(
        self,
        commands: Any,
        unit_id: str,
        source_path: Path,
        source_root: Path,
        manifest_unit: dict[str, Any],
        compile_manifest: dict[str, Any],
        raw_payloads: dict[RawFactKind, ArtifactPayload],
        rebuilt_semantic: dict[str, Any],
    ) -> None:
        if not isinstance(commands, list):
            raise WorkflowError(f"command records are not an array for unit {unit_id}")
        by_kind = self._index(commands, unit_id)
        compiler = compile_manifest["compiler"]
        executable = Path(compiler["resolved_path"]).resolve()
        compile_directory = Path(str(manifest_unit.get("compile_directory", ""))).resolve()
        require_within(source_root, compile_directory, f"compile directory for {unit_id}")
        arguments = manifest_unit.get("arguments")
        if not isinstance(arguments, list) or not all(isinstance(item, str) for item in arguments):
            raise WorkflowError(f"compile arguments are invalid for unit {unit_id}")
        base = GccCompatibleCommand.analysis_base_arguments(
            arguments,
            executable,
            target_triple=compiler["verified_target_triple"],
        )
        for specification in CLANG_EXTRACTIONS:
            self._validate_record(
                by_kind[specification.kind],
                specification,
                unit_id,
                source_path,
                compile_directory,
                [*base, *specification.arguments],
                compiler,
                raw_payloads[specification.kind].data,
                rebuilt_semantic,
            )

    def _validate_record(
        self,
        record: dict[str, Any],
        specification: ExtractionSpec,
        unit_id: str,
        source_path: Path,
        compile_directory: Path,
        expected_argv: list[str],
        compiler: dict[str, Any],
        raw_data: bytes,
        rebuilt_semantic: dict[str, Any],
    ) -> None:
        expected = {
            "schema_version": 1,
            "unit_id": unit_id,
            "source_path": str(source_path),
            "fact_kind": specification.kind,
            "raw_format": specification.format,
            "output_stream": specification.stream,
            "output_sha256": hashlib.sha256(raw_data).hexdigest(),
            "output_size": len(raw_data),
            "analyzer_sha256": compiler["sha256"],
            "target_triple": compiler["verified_target_triple"],
            "argv": expected_argv,
            "cwd": str(compile_directory),
            "exit_code": 0,
            "launched": True,
            "launch_error": None,
            "timed_out": False,
        }
        mismatched = [
            field
            for field, expected_value in expected.items()
            if record.get(field) != expected_value
        ]
        if mismatched:
            raise WorkflowError(
                f"command record differs for {unit_id}:{specification.kind.value}: "
                + ", ".join(sorted(mismatched))
            )
        stdout = self._output(record, OutputStream.STDOUT, unit_id)
        stderr = self._output(record, "stderr", unit_id)
        selected = (
            stdout
            if specification.stream is OutputStream.STDOUT
            else self._combined(stdout, stderr)
        )
        if selected != raw_data:
            raise WorkflowError(
                f"command output differs from raw fact for {unit_id}:{specification.kind.value}"
            )
        self._replay(
            expected_argv,
            compile_directory,
            source_path,
            unit_id,
            specification,
            stdout,
            stderr,
            rebuilt_semantic,
        )

    @staticmethod
    def _replay(
        argv: list[str],
        cwd: Path,
        source_path: Path,
        unit_id: str,
        specification: ExtractionSpec,
        captured_stdout: bytes,
        captured_stderr: bytes,
        rebuilt_semantic: dict[str, Any],
    ) -> None:
        try:
            replay = subprocess.run(
                argv,
                cwd=cwd,
                check=False,
                capture_output=True,
                timeout=120,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise WorkflowError(
                f"cannot replay frozen command for {unit_id}:{specification.kind.value}"
            ) from error
        if replay.returncode != 0:
            raise WorkflowError(
                f"frozen command replay failed for {unit_id}:{specification.kind.value}"
            )
        if replay.stderr != captured_stderr:
            raise WorkflowError(f"replayed stderr differs for {unit_id}:{specification.kind.value}")
        if specification.kind is RawFactKind.TYPED_AST:
            try:
                replay_ast = json.loads(replay.stdout)
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                raise WorkflowError(f"replayed typed AST is invalid for unit {unit_id}") from error
            if not isinstance(replay_ast, dict):
                raise WorkflowError(f"replayed typed AST is not an object for unit {unit_id}")
            replay_semantic = AstSemanticIndexer(unit_id, source_path, replay_ast).build()
            if replay_semantic != rebuilt_semantic:
                raise WorkflowError(f"replayed typed AST semantics differ for unit {unit_id}")
            return
        if replay.stdout != captured_stdout:
            raise WorkflowError(f"replayed stdout differs for {unit_id}:{specification.kind.value}")

    def _output(self, record: dict[str, Any], stream: OutputStream | str, unit_id: str) -> bytes:
        stream_name = stream.value if isinstance(stream, OutputStream) else stream
        path = Path(str(record.get(f"{stream_name}_path", ""))).resolve()
        require_within(self.inventory.project_root, path, f"{stream_name} path")
        if not path.is_file():
            raise WorkflowError(f"command {stream_name} is missing for unit {unit_id}")
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != record.get(f"{stream_name}_sha256"):
            raise WorkflowError(f"command {stream_name} hash differs for unit {unit_id}")
        return data

    @staticmethod
    def _index(commands: list[Any], unit_id: str) -> dict[RawFactKind, dict[str, Any]]:
        result: dict[RawFactKind, dict[str, Any]] = {}
        for record in commands:
            if not isinstance(record, dict):
                raise WorkflowError(f"command record is not an object for unit {unit_id}")
            try:
                kind = RawFactKind(record.get("fact_kind"))
            except (TypeError, ValueError) as error:
                raise WorkflowError(
                    f"command record has invalid fact kind for unit {unit_id}"
                ) from error
            if kind in result:
                raise WorkflowError(f"command record repeats {kind.value} for unit {unit_id}")
            result[kind] = record
        if set(result) != set(RawFactKind):
            raise WorkflowError(f"command records do not cover every raw fact for unit {unit_id}")
        return result

    @staticmethod
    def _combined(stdout: bytes, stderr: bytes) -> bytes:
        return stdout + (b"\n" if stdout and stderr else b"") + stderr
