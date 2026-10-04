"""Offline checks of native evidence parsing; these are not driver validation."""

import importlib.util
import sys
from pathlib import Path

TOOLS = Path(__file__).parents[1] / "scripts/native_experiment"
sys.path.insert(0, str(TOOLS))
spec = importlib.util.spec_from_file_location("native_suite_check", TOOLS / "check.py")
native = importlib.util.module_from_spec(spec)
spec.loader.exec_module(native)
sys.path.remove(str(TOOLS))


def test_skip_and_zero_exit_are_not_native_pass():
    assert not native.summaries("FN_TEST(read)\n", "hwrng tests skipped")["passed"]
    assert not native.summaries(
        "FN_TEST(read)\n", "test_read summary: 0 tests passed, 0 tests failed"
    )["passed"]


def test_every_original_function_must_pass():
    source = "FN_TEST(read)\nFN_TEST(write)\n"
    output = "test_read summary: 12 tests passed, 0 tests failed"
    assert not native.summaries(source, output)["passed"]
    assert not native.summaries(
        source, output + "\ntest_write summary: 1 tests passed, 1 tests failed"
    )["passed"]
    assert native.summaries(
        source, output + "\ntest_write summary: 3 tests passed, 0 tests failed"
    )["passed"]


def test_source_identity_includes_new_driver_files(tmp_path):
    import subprocess

    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "new_driver.rs").write_text("first implementation")
    before = native.source_files(tmp_path)
    (tmp_path / "new_driver.rs").write_text("different implementation")
    assert before != native.source_files(tmp_path)
