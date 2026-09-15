from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..acquisition.models import CheckoutRecord
from .compiler import GccCompatibleCommand


@dataclass(frozen=True, slots=True)
class ClosureContext:
    source: CheckoutRecord
    source_root: Path
    compiler: dict[str, Any]
    compiler_path: Path
    compiler_version: str
    command_adapter: type[GccCompatibleCommand]
    defines: tuple[str, ...]
    resolved_include_paths: tuple[Path, ...]
    conditional_branches: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class TranslationUnitSet:
    records: tuple[dict[str, Any], ...]
    compilation_database: tuple[dict[str, Any], ...]
    source_files: dict[str, Path]
    source_paths: frozenset[Path]
    target_triple: str
    target_abi: dict[str, object]


@dataclass(frozen=True, slots=True)
class CoverageSet:
    records: dict[str, dict[str, Any]]
    source_files: dict[str, Path]
