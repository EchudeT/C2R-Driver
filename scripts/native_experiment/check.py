"""Collect existing upstream oracles; no new driver stimuli or assertion code."""

from pathlib import Path

from common import docker, git, read, sha, write
from oracle import summaries as summaries
from prepare import setup_assets


def source_files(target):
    if (target / ".git").exists():
        names = git(target, "ls-files", "--cached", "--others", "--exclude-standard", "-z").split(
            "\0"
        )
        return {n: sha(target / n) if (target / n).is_file() else None for n in names if n}
    return None


def check(root, name, target, output, mode):
    task = read(root / "config.json")["drivers"][name]
    receipt_path = output / (mode + ".json")
    output = output / mode
    output.mkdir(parents=True, exist_ok=True)
    setup_assets(root, target)
    verify_inputs(root, target)
    before = source_files(target)
    image = sha(root / "shared/initramfs.cpio.gz")
    if image != read(root / "environment.json")["initramfs_sha256"]:
        raise ValueError("Frozen upstream test image changed")
    build = docker(
        root,
        target,
        [
            "cargo",
            "osdk",
            "build",
            "--initramfs=/root/asterinas/target/native-tests/initramfs.cpio.gz",
            "--kcmd-args=console=ttyS0",
            "--kcmd-args=earlycon",
            "--kcmd-args=loglevel=error",
        ],
        output / "build.log",
        network=True,
    )
    result = {
        "mode": mode,
        "driver": name,
        "build": build,
        "tests": [],
        "environment": read(root / "environment.json"),
        "status": "BUILD_FAILED",
    }
    if not build["exit_code"]:
        boot = runtime(root, target, "boot", output / "boot")
        result["boot"] = boot
        result["boot_passed"] = boot["status"] == "PASS"
        result["status"] = "BOOT_FAILED"
        if result["boot_passed"]:
            run_oracles(root, name, target, output, task, result)
            if mode == "negative":
                result["status"] = (
                    "NEGATIVE_CONTROL_PASS"
                    if any(
                        row.get("boot_passed")
                        and row.get("device_present") is False
                        and row["status"] == "FAIL"
                        for row in result["tests"]
                    )
                    else "ORACLE_INSENSITIVE"
                )
            else:
                result["status"] = (
                    "PASS" if all(row["passed"] for row in result["tests"]) else "FAIL"
                )
    verify_inputs(root, target)
    result["source_unchanged"] = before == source_files(target)
    if not result["source_unchanged"]:
        result["status"] = "SOURCE_CHANGED_DURING_CHECK"
    result["artifacts"] = {
        str(p.relative_to(target)): sha(p) for p in (target / "target/osdk").rglob("*.iso")
    }
    write(receipt_path, result)
    print(name, mode, result["status"], flush=True)
    return result


def runtime(root, target, case, output):
    artifact = target / "target/osdk/asterinas-osdk-bin.iso"
    relative = Path("target/native-tests/runs") / output.name
    guest_output = target / relative
    guest_output.mkdir(parents=True, exist_ok=True)
    before = sha(artifact)
    observation = docker(
        root,
        target,
        [
            "python3",
            "/dpf-runner/runtime.py",
            case,
            "/root/asterinas/target/osdk/asterinas-osdk-bin.iso",
            "/root/asterinas/" + str(relative),
        ],
        output.with_suffix(".log"),
        network=case.startswith("iperf3/"),
        timeout=200,
    )
    path = guest_output / "result.json"
    result = read(path) if path.exists() else {"status": "ERROR", "boot_passed": False}
    result.update(command=observation, artifact_sha256=before, evidence=str(guest_output))
    result["passed"] = observation["exit_code"] == 0 and result["status"] == "PASS"
    if sha(artifact) != before:
        raise ValueError("Artifact changed during native test")
    return result


def run_oracles(root, name, target, output, task, result):
    for case in [*task["tests"], *task["benchmarks"]]:
        result["tests"].append(runtime(root, target, case, output / Path(case).stem))


def verify_inputs(root, target):
    profile = read(root / "environment.json")
    if sha(root / "shared/sdk/cargo-osdk") != profile["sdk_sha256"]:
        raise ValueError("The common SDK changed")
    for relative, expected in read(root / "shared/oracle-sources.json").items():
        path = target / relative
        if not path.is_file() or sha(path) != expected:
            raise ValueError("Frozen upstream test/boot source changed: " + relative)
