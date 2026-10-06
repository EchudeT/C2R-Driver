"""Frozen public-fixture inputs shared by the two experiment methods (stdlib only)."""

import hashlib
import json
from pathlib import Path


def prepared_files(root, driver):
    config = json.loads((Path(root) / "config.json").read_text())["drivers"][driver]
    if config.get("suite_kind") != "fixed-public-fixture":
        return {}
    assets = Path(root) / "shared/public-tests" / driver
    return {
        config["fixture_file"]: (assets / config["fixture_asset"]).read_text(),
        ".dpf-output/harness/public/INTERFACE.md": (assets / "INTERFACE.md").read_text(),
    }


def verify(root, target=None):
    root = Path(root)
    environment = json.loads((root / "environment.json").read_text())
    if environment.get("suite_kind") != "fixed-public-fixture":
        return
    from .native_runner import RUNNER

    files = {root / p: digest for p, digest in environment["public_fixture_files"].items()}
    files[RUNNER / "public_runtime.py"] = environment["public_runtime_sha256"]
    for path, expected in files.items():
        with path.open("rb") as stream:
            actual = hashlib.file_digest(stream, "sha256").hexdigest()
        if actual != expected:
            raise ValueError("Frozen public-fixture input changed: " + str(path))
    if target is not None:
        target = Path(target)
        driver = json.loads((target / ".native-task.json").read_text())["driver"]
        for relative, text in prepared_files(root, driver).items():
            if (target / relative).read_text() != text:
                raise ValueError("Frozen public assertion changed: " + relative)
