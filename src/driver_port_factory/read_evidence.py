"""Bounded local text excerpts with exact continuation, no semantic acceptance decision."""

import argparse
import hashlib
import json
from pathlib import Path


def excerpt(line, number, offset, budget):
    """Fit serialized text rather than assuming every character needs six escapes."""
    def part(length):
        return {"line": number, "column": offset, "text": line[offset:offset + length]}
    low, high = 0, min(len(line) - offset, budget)
    while low < high:
        middle = (low + high + 1) // 2
        if len(json.dumps(part(middle), ensure_ascii=False)) + 2 <= budget:
            low = middle
        else:
            high = middle - 1
    return part(low), low


def read_text(path, *, start=1, column=0, budget=6000, contains=None, headings=False,
              expected_sha256=None):
    if start < 1 or column < 0 or not 256 <= budget <= 20000:
        raise ValueError("start >= 1, column >= 0 and budget 256..20000 required")
    if not path.is_file() or path.stat().st_size > 8 * 1024 * 1024:
        raise ValueError("choose a regular text file up to 8 MiB; "
                         "use targeted log tools for larger files")
    with path.open("rb") as stream:
        data = stream.read(8 * 1024 * 1024 + 1)
    if len(data) > 8 * 1024 * 1024:
        raise ValueError("file grew beyond 8 MiB")
    digest = hashlib.sha256(data).hexdigest()
    if expected_sha256 and digest != expected_sha256:
        return {"status": "CONTENT_CHANGED", "sha256": digest, "path": str(path)}
    text = data.decode("utf-8")
    if "\x00" in text:
        raise ValueError("binary input; choose a text report or source file")
    lines = text.split("\n")
    lines = [line + "\n" for line in lines[:-1]] + ([lines[-1]] if lines[-1] else [])
    if start > max(1, len(lines)) or (column and not lines):
        raise ValueError("start outside file; inspect total_lines before selecting a range")
    result = {"status": "READ", "path": str(path.resolve()), "sha256": digest,
              "total_lines": len(lines), "selection": "headings" if headings else (
                  "literal_matches" if contains is not None else "lines"),
              "excerpts": [], "next": None, "semantic_verdict": "NOT_EVALUATED"}
    remaining = budget
    for number, line in enumerate(lines, 1):
        if number < start or (contains is not None and contains not in line):
            continue
        if headings and not line.lstrip().startswith("#"):
            continue
        offset = column if number == start else 0
        if offset > len(line):
            raise ValueError("column outside requested line")
        part, allowance = excerpt(line, number, offset, remaining)
        if not allowance and offset < len(line):
            result["next"] = {"start": number, "column": offset, "expected_sha256": digest}
            break
        result["excerpts"].append(part)
        remaining -= len(json.dumps(part, ensure_ascii=False)) + 2
        if offset + allowance < len(line):
            result["next"] = {"start": number, "column": offset + allowance,
                              "expected_sha256": digest}
            break
    if result['next']:
        # A cursor must retain the query as well as the byte-content identity.
        result['next'].update(contains=contains, headings=headings)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--path", type=Path, required=True)
    parser.add_argument("--start", type=int, default=1)
    parser.add_argument("--column", type=int, default=0)
    parser.add_argument("--budget", type=int, default=6000)
    parser.add_argument("--expected-sha256")
    query = parser.add_mutually_exclusive_group()
    query.add_argument("--contains")
    query.add_argument("--headings", action="store_true")
    try:
        result = read_text(**vars(parser.parse_args(argv)))
    except (OSError, ValueError) as error:
        result = {"status": "READ_ERROR", "reason": str(error)}
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["status"] == "READ" else 2


if __name__ == "__main__":
    raise SystemExit(main())
