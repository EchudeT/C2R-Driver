"""Real, unpaid verification of production DPF runtime dispatch against operator controls."""

import os

from common import read, write

from driver_port_factory.platform import executor
from driver_port_factory.platform.native import case_names
from driver_port_factory.platform.profile import asterinas


def validate(root):
    environment = read(root / "environment.json")
    config = read(root / "config.json")
    for name, task in config["drivers"].items():
        if not task.get("enabled", True):
            continue
        rows = {}
        for mode in ("positive", "negative"):
            target = (
                root / "operator/reference"
                if mode == "positive"
                else root / "operator/hollow" / name / "target"
            )
            artifact = root / "operator" / name / "adapter-runtime.iso"
            # Exercise the final CAS-style artifact path without copying an ISO.
            os.link(target / "target/osdk/asterinas-osdk-bin.iso", artifact)
            profile = asterinas(
                environment["image_id"], environment["image_id"], config["target_revision"], "kvm"
            )
            profile["native"] = {
                "root": str(root),
                "driver": name,
                "environment": environment,
                "task": task,
            }
            results = []
            try:
                for index, case in enumerate(case_names(task)):
                    directory = target / f".dpf-output/adapter-validation/{name}/{mode}/{index}"
                    result = executor.boot(
                        profile, target, directory, artifact, {"native_case": case}, "native"
                    )
                    results.append(result)
                passed = (
                    all(row["status"] == "PASS" for row in results)
                    if mode == "positive"
                    else any(
                        row.get("boot_passed")
                        and row.get("device_present") is False
                        and row["status"] == "FAIL"
                        for row in results
                    )
                )
                rows[mode] = {"matched_control": passed, "tests": results}
                print(name, mode, "adapter", "PASS" if passed else "FAIL", flush=True)
                write(root / "operator" / name / "adapter-validation.json", rows)
                if not passed:
                    raise RuntimeError("Production adapter did not reproduce the expected control")
            finally:
                artifact.unlink(missing_ok=True)
