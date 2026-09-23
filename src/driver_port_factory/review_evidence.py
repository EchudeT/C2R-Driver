"""Read exact versioned source locations; never match report prose or judge semantics.

python -m driver_port_factory.review_evidence --help
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path, PurePosixPath

MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_EXCERPT_LINES = 200
MAX_MATCHES = 30


def _git(repository: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "--no-optional-locks", "-C", str(repository), *args],
                          capture_output=True, timeout=30, check=False)


def locate(*, repository: Path, revision: str, path: str, start: int | None = None,
           end: int | None = None, symbol: str | None = None,
           expected_sha256: str | None = None) -> dict:
    """Use a caller-selected exact path and full commit; read Git blobs, not dirty files."""
    result = {"repository": str(repository.resolve()), "revision": revision, "path": path,
              "semantic_verdict": "NOT_EVALUATED"}

    def status(name, **details):
        return {**result, "status": name, **details}

    relative = PurePosixPath(path)
    if (not path or relative.is_absolute() or ".." in relative.parts
            or relative.as_posix() != path or "\x00" in path):
        return status("INVALID_PATH", reason="Select an exact repository-relative path")
    if not re.fullmatch(r"[0-9a-fA-F]{40}|[0-9a-fA-F]{64}", revision):
        return status("INVALID_REVISION", reason="Use the full frozen commit ID, not a moving ref")
    if (symbol is None) == (start is None):
        return status("INVALID_QUERY", reason="Select either a line range or literal text")
    if symbol is not None and (not symbol or end is not None or "\n" in symbol):
        return status("INVALID_QUERY", reason="Use nonempty single-line literal text without --end")
    if expected_sha256 is not None and not re.fullmatch(r"[0-9a-fA-F]{64}", expected_sha256):
        return status("INVALID_HASH")
    commit = _git(repository, "rev-parse", "--verify", "--end-of-options", revision + "^{commit}")
    if commit.returncode:
        return status("REVISION_NOT_FOUND")
    actual = commit.stdout.decode().strip()
    if actual != revision.lower():
        return status("REVISION_MISMATCH", resolved_commit=actual)
    # Literal pathspecs prevent glob or colon syntax from choosing a different file.
    entry = _git(repository, "ls-tree", "-z", actual, "--", ":(literal)" + path)
    if entry.returncode:
        return status("GIT_ERROR", reason=entry.stderr.decode(errors="replace")[:1000])
    entries = [item for item in entry.stdout.split(b"\0") if item]
    if not entries:
        return status("PATH_NOT_FOUND")
    header, selected = entries[0].split(b"\t", 1)
    mode, kind, oid = header.decode().split()
    if len(entries) != 1 or selected.decode() != path:
        return status("PATH_MISMATCH")
    if mode not in {"100644", "100755"} or kind != "blob":
        return status("NOT_REGULAR_FILE", mode=mode)
    size = _git(repository, "cat-file", "-s", oid)
    if size.returncode:
        return status("GIT_ERROR")
    if int(size.stdout) > MAX_FILE_BYTES:
        return status("FILE_TOO_LARGE", size_bytes=int(size.stdout), limit=MAX_FILE_BYTES)
    blob = _git(repository, "cat-file", "blob", oid)
    if blob.returncode:
        return status("GIT_ERROR")
    data = blob.stdout
    result["content_sha256"] = hashlib.sha256(data).hexdigest()
    result["git_blob"] = oid
    if expected_sha256 and expected_sha256.lower() != result["content_sha256"]:
        return status("HASH_MISMATCH", expected_sha256=expected_sha256)
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return status("NOT_UTF8_TEXT")
    if "\x00" in text:
        return status("NOT_TEXT")
    # Match physical source line numbers (as in nl/sed); form feeds are not newlines.
    lines = text.split("\n")
    if lines[-1] == "":
        lines.pop()
    lines = [line.removesuffix("\r") for line in lines]
    result["total_lines"] = len(lines)
    if symbol is not None:
        matches = [{"line": n, "text": line} for n, line in enumerate(lines, 1) if symbol in line]
        return status("LOCATED" if matches else "NO_LITERAL_MATCH", literal=symbol,
                      match_kind="literal_occurrence_not_semantic_symbol",
                      match_count=len(matches), matches=matches[:MAX_MATCHES],
                      truncated=len(matches) > MAX_MATCHES)
    end = start if end is None else end
    if start < 1 or end < start or end > len(lines):
        return status("OUT_OF_RANGE", requested_start=start, requested_end=end)
    if end - start + 1 > MAX_EXCERPT_LINES:
        return status("RANGE_TOO_WIDE", limit=MAX_EXCERPT_LINES)
    return status("LOCATED", start=start, end=end,
                  lines=[{"line": n, "text": lines[n - 1]} for n in range(start, end + 1)])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", type=Path, required=True)
    parser.add_argument("--revision", required=True, help="Full frozen Git commit ID")
    parser.add_argument("--path", required=True, help="Exact repository-relative path")
    query = parser.add_mutually_exclusive_group(required=True)
    query.add_argument("--start", type=int)
    query.add_argument("--symbol", help="Literal text; no regex or inferred definition matching")
    parser.add_argument("--end", type=int)
    parser.add_argument("--expected-sha256")
    try:
        result = locate(**vars(parser.parse_args(argv)))
    except (OSError, ValueError, UnicodeError, subprocess.SubprocessError) as error:
        result = {"status": "TOOL_ERROR", "reason": str(error), "semantic_verdict": "NOT_EVALUATED"}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "LOCATED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
