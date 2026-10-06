"""Offline adapter checks; no model, kernel build or driver execution."""

import json
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

from driver_port_factory.platform import executor, native


def test_fixed_fixture_is_included_in_the_same_three_case_factory_suite(tmp_path):
    assets = Path(__file__).parents[1] / "src/driver_port_factory/data/public-tests"
    for driver, file in (("evbug", "evbug_public.rs"), ("ne2k-pci", "ne2k_public.rs")):
        source = assets / driver
        shared = tmp_path / "shared/public-tests" / driver
        shared.mkdir(parents=True)
        (shared / file).write_text((source / file).read_text())
        (shared / "INTERFACE.md").write_text((source / "INTERFACE.md").read_text())
        task = {
            "suite_kind": "fixed-public-fixture",
            "fixture_asset": file,
            "fixture_file": "component/" + file,
            "scope": "synthetic adapter check",
            "tests": [r["id"] for r in json.loads((source / "cases.json").read_text())],
            "benchmarks": [],
        }
        (tmp_path / "config.json").write_text(json.dumps({"drivers": {driver: task}}))
        control = tmp_path / driver
        control.mkdir()
        (control / "native-suite.json").write_text(
            json.dumps({"root": str(tmp_path), "driver": driver, "task": task})
        )
        files, rows = native.definition(SimpleNamespace(root=tmp_path, control=control))
        assert len(rows) == 3
        assert files[task["fixture_file"]] == (source / file).read_text()
        assert (
            files[".dpf-output/harness/public/INTERFACE.md"]
            == (source / "INTERFACE.md").read_text()
        )


def test_public_boot_uses_the_frozen_guest_runner_and_same_iso(monkeypatch, tmp_path):
    monkeypatch.setattr(native, "verify_inputs", lambda *args: None)
    monkeypatch.setattr(native, "setup", lambda *args: None)
    artifact = tmp_path / "candidate.iso"
    artifact.write_bytes(b"synthetic ISO")
    seen = []

    @dataclass
    class Receipt:
        exit_code: int = 0
        timed_out: bool = False

    def container(profile, worktree, directory, command, **kwargs):
        seen.append(command)
        (directory / "result.json").write_text('{"status":"PASS"}')
        return Receipt()

    monkeypatch.setattr(executor, "container", container)
    profile = {
        "native": {
            "root": str(tmp_path),
            "task": {
                "runtime_script": "public_runtime.py",
                "boot_case": "evbug-baseline-boot",
                "tests": ["evbug-events"],
                "benchmarks": [],
            },
        }
    }
    receipt = native.boot(
        profile, tmp_path, tmp_path / "run", artifact, {"native_case": "boot"}, "synthetic"
    )
    assert seen[0][1:4] == ["/dpf-runner/public_runtime.py", "evbug-baseline-boot", str(artifact)]
    assert receipt["status"] == "PASS" and receipt["artifact_sha256"]
