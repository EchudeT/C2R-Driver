"""Behavioral risk-routing cases, not a Rust correctness proof."""
import pytest

from driver_port_factory.migration.rust_risk import impacted_risks


def risks(old, new):
    return impacted_risks({"driver.rs": old.encode()}, {"driver.rs": new.encode()})


@pytest.mark.parametrize("tail", [
    "\nfn unrelated() -> u32 { 2 }",
    '\nconst HELP: &str = r###"unsafe { example() }"###;',
    "\n/* unsafe { } /* nested */ */ // unsafe fn example()\n",
])
def test_unrelated_additions_do_not_reopen_old_unsafe(tail):
    old = "unsafe fn hardware() { access(); }"
    assert not risks(old, old + tail)


def test_comments_strings_and_formatting_are_not_unsafe_changes():
    old = 'unsafe fn hardware() { access(); }\nconst HELP: &str = "unsafe fn text";'
    new = '/* explanation */ unsafe fn hardware ( ) {\n access ( ) ; /* why */ }\n' \
          'const HELP: &str = "unsafe fn text";'
    assert not risks(old, new)


@pytest.mark.parametrize("old,new", [
    ("", "unsafe fn hardware() {}"),
    ("unsafe fn hardware() {}", ""),
    ("unsafe fn hardware() { write(1); }", "unsafe fn hardware() { write(2); }"),
    ("unsafe fn hardware() {}", "fn hardware() {}"),
    ("", "unsafe impl Send for Device {}"),
    ("", 'extern "C" { fn foreign(); }'),
])
def test_added_removed_and_changed_boundaries_trigger(old, new):
    assert risks(old, new)


def test_unchanged_cross_file_unsafe_caller_tracks_changed_safe_helper():
    old = {"hw.rs": b"unsafe fn hardware() { limit(); }",
           "helper.rs": b"fn limit() -> usize { bound() } fn bound() -> usize { 1 }"}
    new = {**old, "helper.rs": old["helper.rs"].replace(b"{ 1 }", b"{ 2 }")}
    found = impacted_risks(old, new)
    assert found[0]["scope"] == "bound"
    assert found[0]["boundary"]["path"] == "hw.rs"
    assert found[0]["basis"] == "name_dependency"


def test_changed_safe_wrapper_reaching_unsafe_is_not_exempt():
    old = "fn wrapper() { hardware(); } unsafe fn hardware() {}"
    assert risks(old, old.replace("hardware();", "hardware(); hardware();"))


def test_type_and_callback_dependencies_are_in_scope():
    old = "struct Device { size: u32 } unsafe fn use_device(d: Device) { callback(); } fn callback() {}"
    assert risks(old, old.replace("size: u32", "size: u64"))
    assert risks(old, old.replace("fn callback() {}", "fn callback() { stop(); }"))


def test_inner_cfg_changes_are_not_hidden_by_unchanged_function_text():
    old = "mod device { #![cfg(feature = \"old\")] fn unrelated() {} unsafe fn hardware() {} }"
    assert risks(old, old.replace('"old"', '"new"'))


def test_alias_and_macro_uncertainty_do_not_silently_skip_review():
    old = "use device::hardware as access; unsafe fn hardware() {} fn wrapper() { access(); }"
    assert risks(old, old.replace("access();", "access(); access();"))
    old = "unsafe fn hardware() {} fn configure() { device_macro!(); }"
    assert risks(old, old.replace("device_macro!()", "device_macro!(new)"))
    assert risks("", "fn invalid( {")
    assert risks("", "fn configure() { external_macro!(); }")


def test_git_policy_uses_baseline_and_unchanged_cross_file_callers(tmp_path):
    from tests.test_audit_repairs import git
    from driver_port_factory.migration.implementation import worktree_files
    from driver_port_factory.migration.review_policy import review_decision

    work = tmp_path / "target"
    work.mkdir()
    git(work, "init")
    (work / "hw.rs").write_text("unsafe fn hardware() { helper(); }\n")
    helper = work / "helper.rs"
    helper.write_text("fn helper() -> u32 { 1 }\n")
    git(work, "add", ".")
    git(work, "commit", "-m", "baseline")
    base = git(work, "rev-parse", "HEAD")

    def decide():
        bundle = {"target_worktree": {"path": "target", "base_commit": base},
                  "files": worktree_files(work, base)}
        return review_decision(tmp_path, bundle, ())

    helper.write_text("fn helper() -> u32 { 2 }\n")
    decision = decide()
    assert decision["independent_required"]
    assert decision["reasons"][0]["boundary"]["path"] == "hw.rs"
    helper.write_text("fn helper() -> u32 { 1 } // explanation\n")
    assert not decide()["independent_required"]
