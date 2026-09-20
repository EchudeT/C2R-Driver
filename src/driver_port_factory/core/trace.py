"""Read successful execve calls, including strace's interleaved process records."""

from __future__ import annotations

import re
import json
from collections.abc import Iterable


def exec_arguments(line: str) -> list[str]:
    try:
        argv, _ = json.JSONDecoder().raw_decode(line[line.index("["):])
    except (ValueError, json.JSONDecodeError):
        return []
    return argv if isinstance(argv, list) and all(isinstance(arg, str) for arg in argv) else []


def qemu_experiment(line: str) -> bool:
    """Reject discovery-only commands; smoke oracles remain the harness's responsibility."""
    argv = exec_arguments(line)
    if any(arg in {"--version", "-version", "--help", "-help", "-h", "help", "?"} for arg in argv[1:]):
        return False
    return any(arg in {"-kernel", "-bios", "-pflash", "-cdrom", "-drive", "-blockdev",
                       "-hda", "-hdb", "-hdc", "-hdd", "-fda", "-fdb", "-qtest"}
               and index + 1 < len(argv) for index, arg in enumerate(argv))


def successful_execs(lines: Iterable[str]) -> tuple[tuple[str, str], ...]:
    pending: dict[str, str] = {}
    results = []
    for line in lines:
        prefix = re.match(r"^(\d+)\s+(.*)$", line)
        pid, body = prefix.groups() if prefix else ("", line)
        if body.startswith("execve(") and "<unfinished ...>" in body:
            pending[pid] = body.partition("<unfinished ...>")[0]
            continue
        if body.startswith("<... execve resumed>"):
            body = pending.pop(pid, "") + body.removeprefix("<... execve resumed>")
        match = re.fullmatch(r'execve\("([^"]+)".*\)\s+= 0', body.strip())
        if match:
            results.append((match.group(1), body))
    return tuple(results)
