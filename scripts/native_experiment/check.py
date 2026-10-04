"""Collect existing upstream oracles; no new driver stimuli or assertion code."""

from pathlib import Path

from common import docker, git, read, sha, write
from prepare import setup_assets

from driver_port_factory.platform.native_runner import BUILD, runtime_identity


def source_files(target):
    if (target / ".git").exists():
        names = git(target, "ls-files", "--cached", "--others", "--exclude-standard", "-z").split(
            "\0"
        )
        return {n: sha(target / n) if (target / n).is_file() else None for n in names if n}
    # Operator exports have no Git history. Bind the same source bytes, excluding build data.
    import os

    from prepare import artifact_ignore

    files = {}
    for directory, dirs, names in os.walk(target):
        excluded = artifact_ignore(directory, [*dirs, *names])
        dirs[:] = [d for d in dirs if d not in excluded]
        for name in names:
            path = Path(directory) / name
            if name not in excluded and path.is_file():
                files[str(path.relative_to(target))] = sha(path)
    return files


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
        BUILD,
        output / "build.log",
        network=True,
    )
    result = {
        "mode": mode,
        "driver": name,
        "build": build,
        "tests": [],
        "environment": read(root / "environment.json"),
        "runtime_identity": runtime_identity(),
        "status": "BUILD_FAILED",
    }
    if not build["exit_code"]:
        boot = runtime(root, target, "boot", output / "boot")
        result["boot"] = boot
        result["boot_passed"] = boot["status"] == "PASS"
        result["status"] = "BOOT_FAILED"
        if result["boot_passed"]:
            run_oracles(root, name, target, output, task, result)
            if any(row["artifact_sha256"] != boot["artifact_sha256"] for row in result["tests"]):
                raise ValueError("A complete suite must run on one identical final ISO")
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


def runtime(root, target, case, output, artifact=None):
    import uuid

    artifact = artifact or target / "target/osdk/asterinas-osdk-bin.iso"
    relative = Path("target/native-tests/runs") / (output.name + "-" + uuid.uuid4().hex[:8])
    guest_output = target / relative
    guest_output.mkdir(parents=True, exist_ok=True)
    setup_assets(root, target)
    before = sha(artifact)
    observation = docker(
        root,
        target,
        [
            "python3",
            "/dpf-runner/runtime.py",
            case,
            str(artifact),
            "/root/asterinas/" + str(relative),
        ],
        output.with_suffix(".log"),
        network=case.startswith("iperf3/"),
        timeout=200,
        artifact=artifact,
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
    from driver_port_factory.platform.native_runner import verify_inputs as verify

    verify(root, target)
