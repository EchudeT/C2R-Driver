"""One reviewable baseline prompt, rendered for each task without planning its solution."""

from pathlib import Path
from string import Template


def render(task, workspace, qemu_source, config, shared_assets=None):
    workspace = Path(workspace)
    environment = task.get("environment", {})
    values = dict(config) | environment
    values.update(
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
    )
    template = Path(__file__).with_name("baseline-prompt.md").read_text()
    return Template(template).substitute(values)
