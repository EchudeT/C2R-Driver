"""Offline checks of native evidence parsing; these are not driver validation."""

import importlib.util
import sys
from pathlib import Path

TOOLS = Path(__file__).parents[1] / "scripts/native_experiment"
sys.path.insert(0, str(TOOLS))
spec = importlib.util.spec_from_file_location("native_suite_check", TOOLS / "check.py")
native = importlib.util.module_from_spec(spec)
spec.loader.exec_module(native)
from oracle import summaries

sys.path.remove(str(TOOLS))


def test_skip_and_zero_exit_are_not_native_pass():
    assert not summaries("FN_TEST(read)\n", "hwrng tests skipped")["passed"]
    assert not summaries("FN_TEST(read)\n", "test_read summary: 0 tests passed, 0 tests failed")[
        "passed"
    ]


def test_every_original_function_must_pass():
    source = "FN_TEST(read)\nFN_TEST(write)\n"
    output = "test_read summary: 12 tests passed, 0 tests failed"
    assert not summaries(source, output)["passed"]
    assert not summaries(source, output + "\ntest_write summary: 1 tests passed, 1 tests failed")[
        "passed"
    ]
    assert summaries(source, output + "\ntest_write summary: 3 tests passed, 0 tests failed")[
        "passed"
    ]


def test_source_identity_includes_new_driver_files(tmp_path):
    import subprocess

    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "new_driver.rs").write_text("first implementation")
    before = native.source_files(tmp_path)
    (tmp_path / "new_driver.rs").write_text("different implementation")
    assert before != native.source_files(tmp_path)


def test_native_dpf_uses_shared_image_and_exact_final_artifact(tmp_path):
    from driver_port_factory.platform.native_runner import container_argv as shared
    from driver_port_factory.platform.profile import container_argv

    target = tmp_path / "target"
    artifact = tmp_path / "cas/final.iso"
    environment = {"image_id": "sha256:fixed-image"}
    profile = {
        "native": {"root": str(tmp_path), "driver": "virtio-rng", "environment": environment}
    }
    command = ["python3", "/dpf-runner/runtime.py", "boot", str(artifact)]
    argv = container_argv(
        profile, target, "test", command, build=False, cache_key="unused", artifact=artifact
    )
    assert argv == shared(environment, tmp_path, target, "test", command, artifact=artifact)
    assert f"{artifact}:{artifact}:ro" in argv
    assert argv[-1] == str(artifact)
    assert not any(arg.startswith("dpf-") and ":/root/.rustup" in arg for arg in argv)


def test_native_cases_are_installed_and_final_assertions_cannot_be_replaced(tmp_path):
    import json

    import pytest

    from driver_port_factory.core.models import WorkflowError
    from driver_port_factory.platform import native as integration
    from driver_port_factory.platform import public_tests, suite
    from tests.test_prepared_public_tests import project_at

    project = project_at(tmp_path)
    project.config.driver_name = "virtio-rng"
    config = {
        "driver": "virtio-rng",
        "task": {
            "scope": "synthetic scope for infrastructure verification",
            "tests": ["test/initramfs/src/regression/device/hwrng.c"],
            "benchmarks": [],
        },
    }
    (project.control / "native-suite.json").write_text(json.dumps(config))
    target = tmp_path / "target"
    suite.install(target)
    public_tests.install(project, target)
    public_tests.verify(project, target, final=True)
    assert public_tests.context(project)["cases"] == ["native-virtio-rng-1"]
    profile = {"native": config}
    with pytest.raises(WorkflowError, match="installed native"):
        integration.validate_case(profile, {"steps": [{"wait_serial": "# "}]})
    with pytest.raises(WorkflowError, match="outside"):
        integration.validate_case(profile, {"native_case": "other.c"})
    case = target / ".dpf-output/harness/public/native-virtio-rng-1.json"
    case.write_text('{"native_case":"boot"}')
    with pytest.raises(WorkflowError, match="assertion changed"):
        public_tests.verify(project, target, final=True)


def test_native_profile_selects_published_seed_before_model_work(tmp_path, monkeypatch):
    import json
    from unittest.mock import patch

    import pytest

    from driver_port_factory.core.models import WorkflowError
    from driver_port_factory.platform import native as integration
    from driver_port_factory.platform.native_runner import BUILD, runtime_identity
    from tests.test_prepared_public_tests import project_at

    project_root = tmp_path / "project"
    project_root.mkdir()
    project = project_at(project_root)
    project.config.driver_name = "virtio-rng"
    root = tmp_path / "suite"
    (root / "seeds/virtio-rng").mkdir(parents=True)
    environment = {
        "image_id": "fixed",
        "accelerator": "kvm",
        "runtime_identity": runtime_identity(),
    }
    (root / "environment.json").write_text(json.dumps(environment))
    (root / "config.json").write_text(json.dumps({"drivers": {"virtio-rng": {}}}))
    (root / "seeds/virtio-rng/task.json").write_text(
        json.dumps({"target_root_commit": "sanitized-root"})
    )
    profile = {"image_id": "fixed", "accelerator": "kvm", "target_revision": "original-root"}
    monkeypatch.setenv("DPF_NATIVE_SUITE", str(root))
    with patch.object(integration, "setup") as setup, patch.object(integration, "verify_inputs"):
        with pytest.raises(WorkflowError, match="published seed"):
            integration.configure(project, tmp_path / "target", profile)
        setup.assert_not_called()
        profile["target_revision"] = "sanitized-root"
        integration.configure(project, tmp_path / "target", profile)
        assert profile["build_argv"] == BUILD
        assert profile["native"]["environment"] == environment
        assert integration.binding(project) == profile["native"]
