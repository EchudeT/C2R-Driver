"""Descriptive summaries with every attempt in the denominator, including failure and unknown."""

from statistics import median


def summarize(rows):
    groups = {}
    for row in rows:
        key = (
            row["method"],
            row["driver"],
            row["model"],
            row["reasoning_effort"],
            row.get("source_revision", "unknown"),
            row.get("target_revision", "unknown"),
            row.get("image_id", "unknown"),
            row.get("suite_kind", "unknown"),
        )
        groups.setdefault(key, []).append(row)
    results = []
    for (method, driver, model, effort, source, target, image, kind), members in sorted(
        groups.items()
    ):
        costs = [
            r["usd_estimate"]
            for r in members
            if r.get("usd_estimate") is not None and r.get("usage_status") == "known"
        ]
        seconds = [r["total_seconds"] for r in members if r.get("total_seconds") is not None]
        case_total = sum(r["cases_expected"] for r in members)
        results.append(
            {
                "method": method,
                "driver": driver,
                "model": model,
                "reasoning_effort": effort,
                "source_revision": source,
                "target_revision": target,
                "image_id": image,
                "suite_kind": kind,
                "attempts": len(members),
                "identity_unconfirmed": sum(not r["baseline_identity_confirmed"] for r in members),
                "build_pass_rate": sum(r["build_passed"] is True for r in members) / len(members),
                "build_unknown": sum(r["build_passed"] is None for r in members),
                "suite_pass_rate": sum(r["suite_passed"] is True for r in members) / len(members),
                "case_pass_rate": (
                    sum(r["cases_passed"] for r in members) / case_total if case_total else None
                ),
                "median_usd_known": median(costs) if costs else None,
                "mean_usd_known": sum(costs) / len(costs) if costs else None,
                "cost_unknown_or_partial": len(members) - len(costs),
                "median_total_seconds": median(seconds) if seconds else None,
            }
        )
    return {
        "groups": results,
        "note": (
            "Rates use all recorded attempts, not only successes. Cost means/medians use complete "
            "local estimates and disclose the excluded count. Cases are fixed "
            "executables/scripts/scenarios, "
            "not individual assertions. No significance claim is made."
        ),
    }
