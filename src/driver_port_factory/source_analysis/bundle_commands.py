from __future__ import annotations

from pathlib import Path
from typing import Any

from ..core.models import WorkflowError
from ..knowledge.index import file_sha256
from .bundle_artifacts import StructuredArtifactInventory, require_within
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
        analyzer: dict[str, Any],
        raw_records: dict[str, Any],
    ) -> None:
        if not isinstance(commands, list):
            raise WorkflowError(f"command records are not an array for unit {unit_id}")
        by_kind = self._index(commands, unit_id)
        executable = Path(analyzer["resolved_path"]).resolve()
        compile_directory = Path(str(manifest_unit.get("compile_directory", ""))).resolve()
        require_within(source_root, compile_directory, f"compile directory for {unit_id}")
        arguments = manifest_unit.get("arguments")
        if not isinstance(arguments, list) or not all(isinstance(item, str) for item in arguments):
            raise WorkflowError(f"compile arguments are invalid for unit {unit_id}")
        base = GccCompatibleCommand.analysis_base_arguments(
            arguments,
            executable,
            target_triple=analyzer["requested_target_triple"],
        )
        for specification in CLANG_EXTRACTIONS:
            self._validate_record(
                by_kind[specification.kind],
                specification,
                unit_id,
                source_path,
                compile_directory,
                [*base, *specification.arguments],
                analyzer,
                raw_records[specification.kind.value],
            )

    def _validate_record(
        self,
        record: dict[str, Any],
        specification: ExtractionSpec,
        unit_id: str,
        source_path: Path,
        compile_directory: Path,
        expected_argv: list[str],
        analyzer: dict[str, Any],
        raw_record: dict[str, Any],
    ) -> None:
        expected = {
            "schema_version": 1,
            "unit_id": unit_id,
            "source_path": str(source_path),
            "fact_kind": specification.kind,
            "capture_format": specification.format,
            "output_stream": specification.stream,
            "output_sha256": raw_record.get("capture_sha256"),
            "output_size": raw_record.get("capture_size"),
            "analyzer_sha256": analyzer["sha256"],
            "target_triple": analyzer["observed_target_triple"],
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
        self._output(record, "stderr", unit_id)
        capture = self.inventory.project_path(raw_record.get("capture_path"), "capture path")
        if specification.stream is OutputStream.STDOUT:
            if capture != stdout:
                raise WorkflowError(f"stdout capture path differs for unit {unit_id}")
        else:
            self._verify_file(
                capture,
                raw_record.get("capture_sha256"),
                raw_record.get("capture_size"),
                unit_id,
            )

    def _output(self, record: dict[str, Any], stream: OutputStream | str, unit_id: str) -> Path:
        stream_name = stream.value if isinstance(stream, OutputStream) else stream
        path = Path(str(record.get(f"{stream_name}_path", ""))).resolve()
        require_within(self.inventory.project_root, path, f"{stream_name} path")
        self._verify_file(
            path,
            record.get(f"{stream_name}_sha256"),
            None,
            unit_id,
        )
        return path

    @staticmethod
    def _verify_file(path: Path, digest: Any, size: Any, unit_id: str) -> None:
        if not path.is_file() or file_sha256(path) != digest:
            raise WorkflowError(f"command capture hash differs for unit {unit_id}")
        if size is not None and path.stat().st_size != size:
            raise WorkflowError(f"command capture size differs for unit {unit_id}")

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
