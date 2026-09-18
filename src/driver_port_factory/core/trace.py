"""Read successful execve calls, including strace's interleaved process records."""

from __future__ import annotations

import re
from collections.abc import Iterable


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
