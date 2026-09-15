from __future__ import annotations

import hashlib
import json
import shlex
import subprocess
from enum import StrEnum
from pathlib import Path

from ..core.models import WorkflowError


class CompilerFamily(StrEnum):
    GCC_COMPATIBLE = "gcc-compatible"


class GccCompatibleCommand:
    """Parse and adapt GCC/Clang-compatible argv without interpreting C source text."""

    OPTIONS_WITH_VALUES = frozenset({"-o", "-MF", "-MT", "-MQ"})
    COMPILATION_MODES = frozenset({"-c", "-S", "-E", "-fsyntax-only", "-M", "-MM", "-MD", "-MMD"})
    REQUIRED_ABI_MACROS = frozenset(
        {
            "__BYTE_ORDER__",
            "__ORDER_BIG_ENDIAN__",
            "__ORDER_LITTLE_ENDIAN__",
            "__ORDER_PDP_ENDIAN__",
            "__CHAR_BIT__",
            "__SIZEOF_INT__",
            "__SIZEOF_LONG__",
            "__SIZEOF_LONG_LONG__",
            "__SIZEOF_POINTER__",
            "__SIZEOF_SIZE_T__",
            "__SIZEOF_WCHAR_T__",
            "__BIGGEST_ALIGNMENT__",
        }
    )
    ABI_MACRO_PREFIXES = (
        "__ARM_",
        "__AARCH64_",
        "__MIPS_",
        "__mips_",
        "__riscv_",
        "__WCHAR_",
        "__GCC_ATOMIC_",
    )
    ABI_MACRO_NAMES = REQUIRED_ABI_MACROS | frozenset(
        {
            "__CHAR_UNSIGNED__",
            "__WCHAR_UNSIGNED__",
            "__FLT_EVAL_METHOD__",
            "__SIZEOF_FLOAT__",
            "__SIZEOF_DOUBLE__",
            "__SIZEOF_LONG_DOUBLE__",
            "__SOFTFP__",
        }
    )

    @staticmethod
    def version(executable: Path) -> str:
        completed = subprocess.run(
            [str(executable), "--version"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
        output = (completed.stdout + completed.stderr).strip()
        if completed.returncode != 0 or not output:
            raise WorkflowError("source compiler --version did not return usable identity")
        return output

    @classmethod
    def effective_target_triple(
        cls,
        arguments: list[str],
        compile_directory: Path,
        *,
        executable: Path | None = None,
        target_triple: str | None = None,
    ) -> str:
        command = cls.analysis_base_arguments(
            arguments,
            executable or Path(arguments[0]),
            target_triple=target_triple,
        )
        completed = subprocess.run(
            [*command, "-dumpmachine"],
            cwd=compile_directory,
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
        output = (completed.stdout + completed.stderr).strip()
        if completed.returncode != 0 or not output:
            raise WorkflowError(
                "C compiler effective-target probe failed: "
                + (completed.stderr.strip() or "-dumpmachine returned no target triple")
            )
        return output.splitlines()[-1]

    @staticmethod
    def option_values(arguments: list[str], option: str) -> list[str]:
        values: list[str] = []
        position = 1
        while position < len(arguments):
            argument = arguments[position]
            if argument == option:
                if position + 1 >= len(arguments) or not arguments[position + 1]:
                    raise WorkflowError(f"compile argument {option} has no value")
                values.append(arguments[position + 1])
                position += 2
                continue
            if argument.startswith(option) and len(argument) > len(option):
                values.append(argument[len(option) :])
            position += 1
        return values

    @classmethod
    def resolved_include_arguments(cls, arguments: list[str], compile_directory: Path) -> set[Path]:
        paths: set[Path] = set()
        for value in cls.option_values(arguments, "-I"):
            path = Path(value)
            paths.add((path if path.is_absolute() else compile_directory / path).resolve())
        return paths

    @staticmethod
    def contains_source(arguments: list[str], compile_directory: Path, source_path: Path) -> bool:
        for argument in arguments[1:]:
            if argument.startswith("-"):
                continue
            path = Path(argument)
            resolved = (path if path.is_absolute() else compile_directory / path).resolve()
            if resolved == source_path:
                return True
        return False

    @classmethod
    def analysis_base_arguments(
        cls,
        arguments: list[str],
        analyzer: Path,
        *,
        target_triple: str | None = None,
    ) -> list[str]:
        filtered: list[str] = [str(analyzer)]
        has_explicit_target = False
        position = 1
        while position < len(arguments):
            argument = arguments[position]
            if argument == "-target" or argument.startswith("--target="):
                has_explicit_target = True
            if argument in cls.OPTIONS_WITH_VALUES:
                position += 2
                continue
            if argument in cls.COMPILATION_MODES:
                position += 1
                continue
            if any(
                argument.startswith(option) and len(argument) > len(option)
                for option in cls.OPTIONS_WITH_VALUES
            ):
                position += 1
                continue
            filtered.append(argument)
            position += 1
        if target_triple and not has_explicit_target:
            filtered.insert(1, f"--target={target_triple}")
        return filtered

    @classmethod
    def abi_signature(
        cls,
        arguments: list[str],
        compile_directory: Path,
        *,
        executable: Path | None = None,
        target_triple: str | None = None,
    ) -> dict[str, object]:
        command = cls.analysis_base_arguments(
            arguments,
            executable or Path(arguments[0]),
            target_triple=target_triple,
        )
        completed = subprocess.run(
            [*command, "-dM", "-E"],
            cwd=compile_directory,
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if completed.returncode != 0:
            raise WorkflowError(
                "compiler ABI probe failed: "
                + (completed.stderr.strip() or "preprocessor command failed")
            )
        macros: dict[str, str] = {}
        for line in completed.stdout.splitlines():
            fields = line.split(maxsplit=2)
            if (
                len(fields) == 3
                and fields[0] == "#define"
                and (
                    fields[1] in cls.ABI_MACRO_NAMES
                    or any(fields[1].startswith(prefix) for prefix in cls.ABI_MACRO_PREFIXES)
                )
            ):
                macros[fields[1]] = fields[2]
        missing = sorted(cls.REQUIRED_ABI_MACROS - macros.keys())
        if missing:
            raise WorkflowError("compiler ABI probe omitted macros: " + ", ".join(missing))

        def integer(name: str) -> int:
            try:
                return int(macros[name], 0)
            except ValueError as error:
                raise WorkflowError(f"compiler ABI macro {name} is not an integer") from error

        byte_order_value = macros["__BYTE_ORDER__"]
        byte_orders = {
            macros["__ORDER_LITTLE_ENDIAN__"]: "little",
            macros["__ORDER_BIG_ENDIAN__"]: "big",
            macros["__ORDER_PDP_ENDIAN__"]: "pdp",
            "__ORDER_LITTLE_ENDIAN__": "little",
            "__ORDER_BIG_ENDIAN__": "big",
            "__ORDER_PDP_ENDIAN__": "pdp",
        }
        byte_order = byte_orders.get(byte_order_value)
        if byte_order is None:
            raise WorkflowError("compiler ABI probe returned an unknown byte order")
        char_width = integer("__CHAR_BIT__")
        selected_macros = {
            name: value
            for name, value in sorted(macros.items())
            if name in cls.ABI_MACRO_NAMES
            or any(name.startswith(prefix) for prefix in cls.ABI_MACRO_PREFIXES)
        }
        abi_flags = cls._abi_affecting_arguments(arguments)
        effective_target = target_triple or cls.effective_target_triple(
            arguments, compile_directory, executable=executable
        )
        fingerprint_material = json.dumps(
            {
                "target_triple": effective_target,
                "abi_flags": abi_flags,
                "predefined_macros": selected_macros,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        return {
            "target_triple": effective_target,
            "pointer_width_bits": integer("__SIZEOF_POINTER__") * char_width,
            "long_width_bits": integer("__SIZEOF_LONG__") * char_width,
            "long_long_width_bits": integer("__SIZEOF_LONG_LONG__") * char_width,
            "int_width_bits": integer("__SIZEOF_INT__") * char_width,
            "size_t_width_bits": integer("__SIZEOF_SIZE_T__") * char_width,
            "char_width_bits": char_width,
            "byte_order": byte_order,
            "wchar_width_bits": integer("__SIZEOF_WCHAR_T__") * char_width,
            "biggest_alignment_bytes": integer("__BIGGEST_ALIGNMENT__"),
            "abi_flags": abi_flags,
            "predefined_macros": selected_macros,
            "fingerprint_sha256": hashlib.sha256(fingerprint_material).hexdigest(),
        }

    @staticmethod
    def _abi_affecting_arguments(arguments: list[str]) -> list[str]:
        selected: list[str] = []
        position = 1
        options_with_separate_values = {"-target", "--target", "-march", "-mcpu", "-mabi"}
        while position < len(arguments):
            argument = arguments[position]
            if argument in options_with_separate_values:
                if position + 1 >= len(arguments):
                    raise WorkflowError(f"compile argument {argument} has no value")
                selected.extend((argument, arguments[position + 1]))
                position += 2
                continue
            if argument.startswith(("-m", "--target=", "-fpack-struct")) or argument in {
                "-fshort-enums",
                "-fshort-wchar",
                "-fsigned-char",
                "-funsigned-char",
            }:
                selected.append(argument)
            position += 1
        return selected

    @classmethod
    def dependencies(
        cls,
        arguments: list[str],
        compile_directory: Path,
        source_root: Path,
        source_path: Path,
    ) -> set[Path]:
        filtered = cls.analysis_base_arguments(arguments, Path(arguments[0]))
        completed = subprocess.run(
            [*filtered, "-MM", "-MT", "dpf-source-closure"],
            cwd=compile_directory,
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if completed.returncode != 0:
            diagnostic = completed.stderr.strip() or completed.stdout.strip()
            raise WorkflowError(
                "compiler dependency scan failed: " + (diagnostic or "no diagnostic")
            )
        dependency_text = completed.stdout.replace("\\\n", " ")
        _, separator, dependency_values = dependency_text.partition(":")
        if not separator:
            raise WorkflowError("compiler dependency scan produced no makefile dependency rule")
        dependencies: set[Path] = set()
        for token in shlex.split(dependency_values):
            path = Path(token)
            resolved = (path if path.is_absolute() else compile_directory / path).resolve()
            if resolved == source_path:
                continue
            if resolved == source_root or source_root in resolved.parents:
                if not resolved.is_file():
                    raise WorkflowError(
                        f"compiler dependency scan named a missing source file: {resolved}"
                    )
                dependencies.add(resolved)
        return dependencies


COMPILER_ADAPTERS: dict[CompilerFamily, type[GccCompatibleCommand]] = {
    CompilerFamily.GCC_COMPATIBLE: GccCompatibleCommand,
}


def compiler_adapter(family: CompilerFamily) -> type[GccCompatibleCommand]:
    try:
        return COMPILER_ADAPTERS[family]
    except KeyError as error:
        raise WorkflowError(f"no command adapter for compiler family {family}") from error
