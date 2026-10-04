"""Operator-only positive baseline and sanitized experiment preparation. No model calls."""

import shutil
import subprocess
import tarfile
from pathlib import Path

from common import CONFIG, HERE, docker, git, read, sha, write
from hollow import hollow


def export(repository, revision, target, paths=()):
    target.mkdir(parents=True)
    process = subprocess.Popen(
        ["git", "-C", str(repository), "archive", revision, *paths], stdout=subprocess.PIPE
    )
    try:
        with tarfile.open(fileobj=process.stdout, mode="r|") as archive:
            for member in archive:
                archive.extract(member, target, filter="data")
        if process.wait() != 0:
            raise RuntimeError("Pinned git archive failed")
    finally:
        process.stdout.close()
        if process.poll() is None:
            process.kill()
            process.wait()


def root_commit(target):
    git(target, "init", "-q")
    git(target, "add", ".")
    git(
        target,
        "-c",
        "user.name=Native experiment preparation",
        "-c",
        "user.email=experiment@localhost",
        "commit",
        "-qm",
        "Sanitized experiment baseline",
    )
    return git(target, "rev-parse", "HEAD")


def prepare(root, source, target, qemu):
    if root.exists():
        raise ValueError("Use a new suite directory; existing evidence is never overwritten")
    config = read(CONFIG)
    for repo, key in [
        (source, "source_revision"),
        (target, "target_revision"),
        (qemu, "qemu_revision"),
    ]:
        git(repo, "cat-file", "-e", config[key] + "^{commit}")
    if not Path("/dev/kvm").exists():
        raise ValueError("KVM is required; no TCG fallback")
    root.mkdir(parents=True)
    (root / "shared").mkdir()
    write(root / "config.json", config)
    image = subprocess.check_output(
        ["docker", "image", "inspect", "--format", "{{.Id}}", config["image"]], text=True
    ).strip()
    write(
        root / "environment.json",
        {
            "image_id": image,
            "image_tag": config["image"],
            "source_revision": config["source_revision"],
            "target_revision": config["target_revision"],
            "qemu_evidence_revision": config["qemu_revision"],
            "accelerator": "kvm",
            "cpus": 1,
            "memory": "2G",
            "firmware": "/root/ovmf/release/OVMF.fd",
            "machine": "q35",
        },
    )
    original = root / "operator/reference"
    print("Exporting pinned original reference (operator only)", flush=True)
    export(target, config["target_revision"], original)
    frozen = {}
    for name, task in config["drivers"].items():
        directory = original / task["omit"]
        frozen[name] = {
            str(p.relative_to(original)): sha(p) for p in directory.rglob("*") if p.is_file()
        }
    write(
        root / "operator/ground-truth.json", {"commit": config["target_revision"], "files": frozen}
    )
    manifest = original / "OSDK.toml"
    manifest.write_text(manifest.read_text().replace('init_args = ["sh", "-l"]', "init_args = []"))
    sdk = root / "shared/sdk"
    sdk.mkdir()
    existing_sdk = target / "osdk/target/release/cargo-osdk"
    if existing_sdk.is_file():
        print("Reusing the existing local-development SDK", flush=True)
        shutil.copy2(existing_sdk, sdk / "cargo-osdk")
    else:
        print("Building the pinned SDK once (OSDK_LOCAL_DEV=1)", flush=True)
        receipt = docker(
            root,
            original,
            ["cargo", "build", "--release"],
            root / "operator/sdk.log",
            workdir="osdk",
            network=True,
        )
        if receipt["exit_code"]:
            raise RuntimeError("SDK build failed: " + receipt["log"])
        shutil.copy2(original / "osdk/target/release/cargo-osdk", sdk / "cargo-osdk")
    write(
        root / "shared/sdk.json",
        {
            "sha256": sha(sdk / "cargo-osdk"),
            "source": "existing-local-sdk" if existing_sdk.is_file() else "pinned-source-build",
        },
    )
    tests = [p for t in config["drivers"].values() if t.get("enabled", True) for p in t["tests"]]
    benchmarks = [
        p for t in config["drivers"].values() if t.get("enabled", True) for p in t["benchmarks"]
    ]
    write(
        root / "shared/package-task.json", {"native_tests": tests, "native_benchmarks": benchmarks}
    )
    print("Packaging unchanged upstream tests and disposable disks", flush=True)
    receipt = docker(
        root,
        original,
        ["python3", "/dpf-runner/package_tests.py"],
        root / "operator/packaging.log",
        network=True,
    )
    if receipt["exit_code"]:
        raise RuntimeError("Packaging failed: " + receipt["log"])
    for name in ("initramfs.cpio.gz", "manifest.json"):
        shutil.copy2(original / "target/native-tests" / name, root / "shared" / name)
    shutil.copytree(
        original / "test/initramfs/build", root / "shared/disks", copy_function=copy_sparse
    )
    freeze_oracles(root, original)
    print("Recording container toolchain and firmware identities", flush=True)
    receipt = docker(
        root,
        original,
        [
            "bash",
            "-lc",
            (
                "set -e; rustc -Vv; cargo -V; qemu-system-x86_64 --version; "
                'sha256sum /root/ovmf/release/OVMF.fd "$(command -v qemu-system-x86_64)"; '
                "cat /root/asterinas/rust-toolchain.toml"
            ),
        ],
        root / "operator/tools.log",
    )
    if receipt["exit_code"]:
        raise RuntimeError("Tool identity capture failed")
    profile = read(root / "environment.json")
    profile.update(
        {
            "sdk_sha256": sha(sdk / "cargo-osdk"),
            "initramfs_sha256": sha(root / "shared/initramfs.cpio.gz"),
            "tools_log_sha256": sha(root / "operator/tools.log"),
            "runner": {p.name: sha(p) for p in HERE.glob("*.py")},
        }
    )
    write(root / "environment.json", profile)
    print(
        "READY for original positive controls; no target has been released to a model", flush=True
    )


def copy_sparse(source, destination):
    subprocess.run(
        ["cp", "--sparse=always", "--reflink=auto", str(source), str(destination)], check=True
    )
    return str(destination)


def create_hollow(root, name):
    config = read(root / "config.json")
    task = config["drivers"][name]
    if not task.get("enabled", True):
        raise ValueError(task["reason"])
    positive = read(root / "operator" / name / "positive.json")
    if positive["status"] != "PASS":
        raise ValueError("The original complete public oracle must pass before removal")
    directory = root / "operator/hollow" / name
    if directory.exists():
        raise ValueError("Hollow baseline already exists")
    target = directory / "target"
    # Export committed upstream bytes; no cached binaries or Git object sharing.
    reference = root / "operator/reference"
    # Reference has no Git history. Copy only tracked upstream paths from source manifest.
    target.mkdir(parents=True)
    ignored = {"target", ".git"}
    for p in reference.iterdir():
        if p.name in ignored:
            continue
        if p.is_dir():
            shutil.copytree(p, target / p.name, symlinks=True, ignore=artifact_ignore)
        else:
            shutil.copy2(p, target / p.name)
    hollow(target, name, task)
    for folder in ("target/native-tests", "test/initramfs/build"):
        (target / folder).mkdir(parents=True, exist_ok=True)
    copy_sparse(root / "shared/initramfs.cpio.gz", target / "target/native-tests/initramfs.cpio.gz")
    for disk in (root / "shared/disks").glob("*.img"):
        copy_sparse(disk, target / "test/initramfs/build" / disk.name)
    write(directory / "task.json", task)
    print("Created operator-only hollow baseline:", target, flush=True)
    return target


def publish(root, name, source):
    config = read(root / "config.json")
    task = config["drivers"][name]
    negative = read(root / "operator" / name / "negative.json")
    if negative["status"] != "NEGATIVE_CONTROL_PASS":
        raise ValueError("Must build, boot, and reject the absent driver before publishing")
    original = root / "operator/hollow" / name / "target"
    seed = root / "seeds" / name
    seed.mkdir(parents=True)
    # No original objects, compile products, operator logs or hidden Git alternates are exported.
    shutil.copytree(original, seed / "target", symlinks=True, ignore=artifact_ignore)
    base = root_commit(seed / "target")
    common = ["include", "drivers/virtio"]
    paths = list(dict.fromkeys([*task["source_entries"], *common]))
    # Git archive tolerates overlapping pathspecs; Linux contains no target Rust implementation.
    export(source, config["source_revision"], seed / "source", paths)
    source_base = root_commit(seed / "source")
    write(
        seed / "task.json",
        {
            "driver": name,
            **task,
            "upstream_linux": config["source_revision"],
            "upstream_asterinas": config["target_revision"],
            "target_root_commit": base,
            "source_root_commit": source_base,
            "environment": read(root / "environment.json"),
        },
    )
    print("Published one sanitized seed; trials are created only when started:", name, flush=True)


def create_trial(root, name, method):
    seed = root / "seeds" / name
    task = read(seed / "task.json")
    trial = root / "trials" / method / name
    trial.mkdir(parents=True, exist_ok=False)
    for role in ("target", "source"):
        subprocess.run(
            ["git", "clone", "-q", "--no-hardlinks", str(seed / role), str(trial / role)],
            check=True,
        )
        git(trial / role, "remote", "remove", "origin")
    shutil.copy2(seed / "task.json", trial / "task.json")
    setup_assets(root, trial / "target")
    write(
        trial / "baseline.json",
        {
            "method": method,
            "driver": name,
            "target_commit": task["target_root_commit"],
            "source_commit": task["source_root_commit"],
            "state": "PREPARED_NOT_TRANSLATED",
        },
    )


def setup_assets(root, target):
    (target / "target/native-tests").mkdir(parents=True, exist_ok=True)
    (target / "test/initramfs/build").mkdir(parents=True, exist_ok=True)
    copy_sparse(root / "shared/initramfs.cpio.gz", target / "target/native-tests/initramfs.cpio.gz")
    for disk in (root / "shared/disks").glob("*.img"):
        copy_sparse(disk, target / "test/initramfs/build" / disk.name)


def artifact_ignore(directory, names):
    root = Path(directory)
    excluded = {n for n in names if n in {".git", "target", "__pycache__", ".dpf-output"}}
    if root.name == "initramfs" and "build" in names:
        excluded.add("build")
    excluded.update(n for n in names if n.startswith("qemu") and n.endswith(".log"))
    return excluded


def freeze_oracles(root, original):
    paths = [original / "OSDK.toml", original / "tools/qemu_args.sh"]
    paths += list((original / "test/initramfs/src").rglob("*"))
    paths += list((original / "tools/net").rglob("*"))
    files = {str(p.relative_to(original)): sha(p) for p in paths if p.is_file()}
    write(root / "shared/oracle-sources.json", files)
