"""Interpret the pinned upstream test.h summaries; skipped tests never pass."""

import re


def summaries(source, output):
    expected = re.findall(r"^FN_TEST\((\w+)\)", source, re.MULTILINE)
    observed = {
        n: (int(p), int(f))
        for n, p, f in re.findall(
            r"test_(\w+) summary: (\d+) tests passed, (\d+) tests failed", output
        )
    }
    return {
        "expected": expected,
        "observed": observed,
        "passed": bool(expected)
        and all(n in observed and observed[n][0] > 0 and observed[n][1] == 0 for n in expected),
    }
