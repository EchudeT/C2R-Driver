"""Pure usage and outcome normalization. No model requests, tests or delivery gates."""

FIELDS = ("input_tokens", "cached_input_tokens", "output_tokens")
CAMEL = ("inputTokens", "cachedInputTokens", "outputTokens")


def normalized_usage(raw):
    if not isinstance(raw, dict):
        return None
    values = {key: raw.get(key, raw.get(camel)) for key, camel in zip(FIELDS, CAMEL)}
    if any(type(value) is not int or value < 0 for value in values.values()):
        return None
    if values["cached_input_tokens"] > values["input_tokens"]:
        return None
    values["cache_write_input_tokens"] = raw.get(
        "cache_write_input_tokens", raw.get("cacheWriteInputTokens", 0)
    )
    if (
        type(values["cache_write_input_tokens"]) is not int
        or values["cache_write_input_tokens"] < 0
    ):
        return None
    return values


def quote(usage, rates):
    if usage is None or usage.get("cache_write_input_tokens", 0):
        return None
    uncached = usage["input_tokens"] - usage["cached_input_tokens"]
    return round(
        (
            uncached * rates["input"]
            + usage["cached_input_tokens"] * rates["cached_input"]
            + usage["output_tokens"] * rates["output"]
        )
        / 1_000_000,
        8,
    )


def usage_from_jobs(jobs):
    total = dict.fromkeys((*FIELDS, "cache_write_input_tokens"), 0)
    unknown = 0
    for job in jobs:
        usage = normalized_usage(job.get("usage"))
        if usage is None:
            unknown += 1
        else:
            for field in total:
                total[field] += usage[field]
    return (
        total if any(normalized_usage(job.get("usage")) is not None for job in jobs) else None
    ), unknown


def native_outcome(receipt, expected_cases):
    tests = receipt.get("tests", [])
    expected = set(expected_cases)
    # Test identity, not temporary paths, determines which fixed cases were observed.
    observed = {row.get("case"): row for row in tests}
    passed = sum(observed.get(case, {}).get("passed") is True for case in expected)
    build = receipt.get("build", {})
    complete = bool(expected) and expected <= set(observed)
    return {
        "build_passed": build.get("exit_code") == 0,
        "boot_passed": receipt.get("boot_passed") is True,
        "cases_expected": len(expected),
        "cases_passed": passed,
        "cases_observed": len(expected & set(observed)),
        "suite_passed": complete and passed == len(expected) and receipt.get("status") == "PASS",
        "artifact_sha256": (receipt.get("boot") or {}).get("artifact_sha256"),
        "evidence_status": receipt.get("status", "UNKNOWN"),
    }


def dpf_outcome(report, build, expected_count):
    run = report.get("run", {})
    cases = run.get("cases", [])
    passed = sum(case.get("status") == "PASS" for case in cases)
    artifact = run.get("runtime_artifact", {}).get("sha256")
    bound_build = bool(artifact) and artifact in {
        build.get("published_sha256"),
        build.get("artifact_sha256"),
    }
    return {
        "build_passed": build.get("status") == "PASS" and bound_build,
        "boot_passed": None,
        "cases_expected": expected_count,
        "cases_passed": passed,
        "cases_observed": len(cases),
        "suite_passed": (
            run.get("final_suite") is True
            and report.get("execution_status") == "PASS"
            and len(cases) == expected_count
            and passed == expected_count
            and expected_count > 0
        ),
        "artifact_sha256": artifact,
        "evidence_status": report.get("execution_status", "NOT_RUN"),
    }


def empty_outcome(expected_count):
    return {
        "build_passed": None,
        "boot_passed": None,
        "cases_expected": expected_count,
        "cases_passed": 0,
        "cases_observed": 0,
        "suite_passed": False,
        "artifact_sha256": None,
        "evidence_status": "NOT_RUN",
    }


def complete_final_batch(report, expected_count):
    run = report.get("run", {})
    return (
        run.get("final_suite") is True
        and expected_count > 0
        and len(run.get("cases", [])) == expected_count
    )


class GoalState:
    """App-server total counters replace the previous value; they are never summed."""

    def __init__(self):
        self.usage = None
        self.goal = None
        self.turn_id = None
        self.turns_started = 0
        self.turn_active = False
        self.failed = False

    def observe(self, message):
        method, params = message.get("method"), message.get("params", {})
        if method == "thread/tokenUsage/updated":
            self.usage = normalized_usage(params.get("tokenUsage", {}).get("total"))
        elif method == "thread/goal/updated":
            self.goal = params["goal"]
        elif method == "turn/started":
            self.turn_id = params["turn"]["id"]
            self.turn_active = True
            self.turns_started += 1
        elif method == "turn/completed":
            self.turn_active = False
            self.failed = params["turn"].get("status") == "failed"

    def stop_reason(self, seconds, limits, rates):
        if limits["seconds"] is not None and seconds >= limits["seconds"]:
            return "TIME_LIMIT"
        cost = quote(self.usage, rates)
        if (
            limits["usd"] is not None
            and not self.turn_active
            and cost is not None
            and cost >= limits["usd"]
        ):
            return "COST_LIMIT"
        if self.failed:
            return "MODEL_FAILED"
        status = (self.goal or {}).get("status")
        if status and status != "active" and not self.turn_active:
            return "GOAL_" + status.upper()
        return None
