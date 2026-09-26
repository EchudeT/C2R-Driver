"""Conservative, generic checks for real-runtime delivery premises.

These checks do not identify a particular driver API and do not decide whether
the translated hardware logic is correct. They only reject evidence that is
mechanically incapable of proving a current runtime: a high-confidence text
marker in the artifact. Shell-level absence is advisory: helpers and external
QMP controllers may supply continuation or runtime binding at execution time.
"""
from __future__ import annotations

import re
import shlex
from pathlib import Path
from typing import Any

_MARKER_WORDS = ("snapshot", "marker", "pci match", "implementation")


def _runtime_marker(runtime: Path, smoke_text: str) -> bool:
    """Recognise only the high-confidence fake marker shape.

    A small text file is not rejected by itself: valid target routes may use a
    script or configuration package. The failed run used a printable marker
    with several marker terms, which is safe to reject before QEMU.
    """
    if runtime.stat().st_size >= 4096:
        return False
    data = runtime.read_bytes()
    if not data:
        return False  # non-empty validation reports an empty artifact separately
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return False
    printable = all(char in "\n\r\t" or 0x20 <= ord(char) < 0x7f for char in text)
    if not printable or len(data) >= 4096 or text.startswith("#!"):
        return False
    if sum(word in text.lower() for word in _MARKER_WORDS) < 2:
        return False
    # A textual package may be a valid input to an argument such as
    # ``-append``. Only reject it when the harness binds it to a QEMU boot or
    # storage argument whose value is expected to be an image/package.
    flags = {
        "-kernel", "-bios", "-pflash", "-cdrom", "-hda", "-hdb", "-hdc",
        "-hdd", "-fda", "-fdb", "-drive",
    }
    for _number, _line, tokens in _active_script_lines(smoke_text):
        if not _is_qemu_invocation(tokens):
            continue
        for index, token in enumerate(tokens):
            if token not in flags or index + 1 >= len(tokens):
                continue
            value = tokens[index + 1]
            if "DPF_RUNTIME_ARTIFACT" in value or "runtime-artifact" in value:
                return True
        if _line_binds_runtime(_line):
            return True
    return False


def _active_script_lines(text: str):
    """Yield parseable, non-comment shell lines with source locations.

    This is deliberately a small lexer rather than a shell interpreter. A
    preflight must not claim that a comment, an echoed example, or an ordinary
    argument is an executed QEMU action. Lines that need shell expansion the
    lexer cannot safely parse are ignored and remain the responsibility of the
    execution observer.
    """
    for number, line in enumerate(text.splitlines(), 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        try:
            tokens = shlex.split(line, comments=True, posix=True)
        except ValueError:
            continue
        if tokens:
            yield number, line, tokens


def _is_qemu_invocation(tokens: list[str]) -> bool:
    if Path(tokens[0]).name.lower() in {"echo", "printf", "cat", "true", "false"}:
        return False
    return any(
        _token_contains_executed_qemu(token)
        for token in tokens
    )


def _token_contains_executed_qemu(token: str) -> bool:
    if Path(token).name.startswith("qemu-system"):
        return True
    display_commands = {"echo", "printf", "cat", "true", "false"}
    for match in re.finditer(
        r"(?:^|[\s;|])qemu-system-[A-Za-z0-9_.-]+(?:$|[\s;|])", token
    ):
        command_segment = token[:match.start()].rsplit(";", 1)[-1].rsplit("|", 1)[-1]
        command_words = command_segment.strip().split()
        if not command_words or Path(command_words[0]).name.lower() not in display_commands:
            return True
    return False


def _line_binds_runtime(line: str) -> bool:
    flags = r"(?:-kernel|-bios|-pflash|-cdrom|-hda|-hdb|-hdc|-hdd|-fda|-fdb|-drive)"
    return bool(
        re.search(
            rf"{flags}\s+[^\s;|]*(?:DPF_RUNTIME_ARTIFACT|runtime-artifact)[^\s;|]*",
            line,
        )
    )


def _has_active_runtime_binding(lines) -> bool:
    return any(
        "DPF_RUNTIME_ARTIFACT" in token
        for _number, _line, tokens in lines
        for token in tokens
    )


def _has_active_continuation(lines) -> bool:
    """Recognise a command-like continuation, excluding comments.

    The check is intentionally permissive after lexing: a project may issue
    QMP through a helper, pipe it through ``nc``/``socat``, or use a small
    wrapper named ``qmp-cont``. The important false-positive guard is that
    prose and comments never count as execution.
    """
    continuation_tokens = {"cont", "continue", "qmp-cont", "qmp_continue"}
    for _number, _line, tokens in lines:
        normalized = [token.strip("{}[](),;\"").lower() for token in tokens]
        if any(token in {"qmp-cont", "qmp_continue"} for token in normalized):
            return True
        command = Path(tokens[0]).name.lower()
        if (
            command in {"cont", "continue", "qmp", "qmp-shell", "qmp-cont"}
            and any(token in continuation_tokens for token in normalized)
        ):
            return True
        if any(
            name in {"nc", "netcat", "socat", "qmp-shell"}
            for name in (Path(token).name.lower() for token in tokens)
        ) and any(token in {"cont", "continue"} for token in normalized):
            return True
        if (
            re.search(r"[\"']?execute[\"']?\s*[:=]\s*[\"']?cont\b", _line, re.IGNORECASE)
            and re.search(r"\b(qmp|nc|netcat|socat)\b", _line, re.IGNORECASE)
        ):
            return True
    return False


def _script_location(
    path: Path, number: int, text: str, *, code: str, detail: str
) -> dict[str, Any]:
    return {
        "kind": "harness",
        "path": str(path),
        "line": number,
        "text": text.strip(),
        "code": code,
        "detail": detail,
    }


def inspect_implementation(project, worktree: Path, base: str) -> dict[str, Any]:
    """Inspect runtime and harness premises before invoking QEMU.

    ``project`` and ``base`` remain part of the interface for future target
    route checks. No source-name or device-name heuristic is used here.
    """
    del project, base
    result: dict[str, Any] = {"status": "PASS", "findings": [], "scope": "runtime-premises"}
    output = worktree / ".dpf-output"
    runtime = output / "runtime-artifact"
    smoke = output / "implementation-smoke.sh"
    smoke_text = (
        smoke.read_text(encoding="utf-8", errors="replace") if smoke.is_file() else ""
    )
    if runtime.is_file() and _runtime_marker(runtime, smoke_text):
        lines = runtime.read_text(encoding="utf-8", errors="replace").splitlines()
        first_line = next((i for i, value in enumerate(lines, 1) if value.strip()), 1)
        result["findings"].append({
            "kind": "artifact",
            "path": str(runtime),
            "line": first_line,
            "text": lines[first_line - 1].strip() if lines else "",
            "code": "non-bootable-runtime-marker",
            "detail": (
                "runtime-artifact is a small printable marker containing multiple marker terms; "
                "it is not evidence of a bootable or packaged runtime artifact"
            ),
        })

    if smoke.is_file():
        text = smoke_text
        lines = text.splitlines()
        active_lines = list(_active_script_lines(text))
        qemu_lines = [item for item in active_lines if _is_qemu_invocation(item[2])]
        if qemu_lines and not _has_active_runtime_binding(active_lines):
            qemu_line, qemu_source, _tokens = qemu_lines[0]
            result["findings"].append(_script_location(
                smoke, qemu_line, qemu_source, code="runtime-not-bound",
                detail="smoke does not bind DPF_RUNTIME_ARTIFACT",
            ))
        # -S only pauses QEMU when no continuation is issued. Search the
        # active script for a continuation, but report the exact -S line.
        if qemu_lines and not _has_active_continuation(active_lines):
            for number, line, tokens in qemu_lines:
                if "-S" in tokens or re.search(r"(?:^|\s)-S(?:$|\s)", line):
                    result["findings"].append(_script_location(
                        smoke, number, line, code="qemu-paused",
                        detail=(
                            "QEMU is started with -S and the script contains no "
                            "continuation/QMP command"
                        ),
                    ))
                    break

    for finding in result["findings"]:
        finding["severity"] = (
            "error" if finding["code"] == "non-bootable-runtime-marker" else "advisory"
        )
    result["status"] = (
        "FAIL" if any(f["severity"] == "error" for f in result["findings"]) else "PASS"
    )
    return result


def format_findings(preflight: dict[str, Any]) -> str:
    """Format all findings with exact paths/lines and their reasons."""
    if not preflight.get("findings"):
        return "implementation runtime preflight passed"
    lines = ["implementation runtime preflight findings (advisories do not block execution):"]
    for finding in preflight["findings"]:
        location = finding.get("path", "<unknown>")
        if finding.get("line") is not None:
            location += f":{finding['line']}"
        lines.append(
            f"- [{finding.get('severity', 'error')}] {location}: "
            f"{finding.get('detail', finding.get('code', 'finding'))}; "
            f"source: {finding.get('text', '<unavailable>')!r}"
        )
    lines.append(
        "Repair the cited implementation, artifact or harness input and resubmit. "
        "If the cited absence is a target capability or packaging route, submit "
        "rework for the earliest affected permitted stage; do not claim PASS from a marker."
    )
    return "\n".join(lines)
