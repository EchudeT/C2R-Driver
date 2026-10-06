"""One reviewable baseline prompt, rendered for each task without planning its solution."""

from pathlib import Path
from string import Template


def render(task, workspace, qemu_source, config, shared_assets=None):
    workspace = Path(workspace)
    environment = task.get("environment", {})
    values = dict(config) | environment
    values.update(
        kernel_log_level=environment.get(
            "kernel_log_level", config.get("kernel_log_level", "error")
        ),
        driver=task["driver"],
        workspace=str(workspace),
        source=str(workspace / "source"),
        target=str(workspace / "target"),
        target_location=str(workspace / "target" / task["omit"]),
        source_entries="\n".join(f"- `{workspace / 'source' / p}`" for p in task["source_entries"]),
        public_cases="\n".join(f"- `{p}`" for p in [*task["tests"], *task["benchmarks"]]),
        scope=task["scope"],
        linux_revision=task.get("upstream_linux", config["source_revision"]),
        target_revision=task.get("upstream_asterinas", config["target_revision"]),
        image_tag=environment.get("image_tag", config["image"]),
        firmware=environment.get("firmware", "/root/ovmf/release/OVMF.fd"),
        qemu_source=str(qemu_source),
        qemu_revision=environment.get("qemu_evidence_revision", config["qemu_revision"]),
        qemu_model=task["qemu_model"],
        shared_assets=str(shared_assets or Path("/experiment-assets")),
        target_baseline=task.get("target_baseline", "only this driver has been removed"),
        public_interface="",
        runtime_profile_rule="Retain the upstream OVMF/device profile.",
    )
    if task.get("suite_kind") == "fixed-public-fixture":
        values["machine"] = task["machine"]
        values["public_interface"] = (
            f"Fixed Rust test source: `{workspace / 'target' / task['fixture_file']}`. "
            f"Read `{workspace / 'target/.dpf-output/harness/public/INTERFACE.md'}` for "
            "the real driver/proc interface. The file also contains Factory-specific tool advice; "
            "no such tools are supplied to this baseline. Adapt the interface, not the assertions. "
            "Only the unchanged fixture may emit its test success markers. Raw public cases and "
            "QEMU profiles are in the public-tests directory of the shared assets. The operator "
            "runs the existing guest assertions and any declared external peer "
            "at final acceptance."
        )
        values["runtime_profile_rule"] = (
            "Use the fixed QEMU profiles in /experiment-assets/public-tests/index.json. "
            "Retain the declared machine, device and network settings for each case. "
            "Do not substitute devices or claim coverage beyond the frozen scope."
        )
    template = Path(__file__).with_name("baseline-prompt.md").read_text()
    return Template(template).substitute(values)
