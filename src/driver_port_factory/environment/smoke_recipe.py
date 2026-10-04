"""Prepare and bind a local container recipe. A recipe never carries a passing verdict."""

import json
import platform
import re
from pathlib import Path

from ..acquisition.repository import load_repository_acquisition
from ..core.container_policy import is_asterinas_dev_image, target_requires_asterinas_container
from ..core.execution import CommandRunner
from ..core.models import ActorRole, EvaluationMode, StageStatus, WorkflowError
from ..knowledge.index import file_sha256
from .contracts import EnvironmentStage
from .smoke_template import render

NAME = "environment-container.json"


def read_recipe(path):
    value = json.loads(Path(path).read_text())
    if (
        not isinstance(value, dict)
        or value.get("schema_version") != 1
        or not isinstance(value.get("binding"), dict)
        or value.get("accelerator") not in {None, "kvm", "tcg"}
        or not isinstance(value.get("image"), str)
        or not re.fullmatch(r"sha256:[0-9a-f]{64}", str(value.get("image_id", "")))
        or type(value.get("timeout_seconds")) is not int
        or not 1 <= value["timeout_seconds"] <= 600
    ):
        raise WorkflowError("Invalid environment recipe identity or timeout")
    return value


def binding(project):
    acquisition = load_repository_acquisition(project)
    return {
        "target": project.config.target_platform,
        "host_architecture": platform.machine(),
        "repositories": {
            c.role.value: c.resolved_commit
            for c in acquisition.checkouts
            if c.role.value in {"target", "qemu"}
        },
    }


def image_identity(project, image):
    if not isinstance(image, str) or not image or image.startswith("-"):
        raise WorkflowError("Select a local image reference, not Docker options")
    if target_requires_asterinas_container(
        project.config.target_platform
    ) and not is_asterinas_dev_image(image):
        raise WorkflowError("Asterinas requires its official asterinas/dev image")
    result = CommandRunner(project.control / "environment" / "image-inspect").run(
        ["docker", "image", "inspect", "--format", "{{.Id}}", image],
        cwd=project.root,
        timeout_seconds=15,
    )
    identity = Path(result.stdout_path).read_text().strip()
    if result.exit_code or not re.fullmatch(r"sha256:[0-9a-f]{64}", identity):
        raise WorkflowError(
            f"Selected local image unavailable; no pull or fallback. {result.stderr_path}"
        )
    return identity


def prepare(project, probe, *, image=None, recipe=None, timeout=None):
    project.ensure_role(ActorRole.DEVELOPER, ActorRole.MIGRATION_OPERATOR)
    if project.stage(EnvironmentStage.RECOVERY).status is not StageStatus.RUNNING:
        raise WorkflowError("Prepare smoke only in the active environment_recovery stage")
    root = project.root / "work/stage-work/environment_recovery"
    root.mkdir(parents=True, exist_ok=True)
    probe = (root / probe).resolve()
    if root not in probe.parents or not probe.is_file() or probe.name == "environment-smoke.sh":
        raise WorkflowError(
            "Probe must be a separate regular shell script in the environment workspace"
        )
    expected = None
    if recipe:
        if project.config.evaluation_mode is not EvaluationMode.DEVELOPER_EVIDENCE:
            raise WorkflowError("Cross-task environment recipes are disabled for blind evaluation")
        prior = read_recipe(recipe)
        if prior.get("schema_version") != 1 or prior.get("binding") != binding(project):
            raise WorkflowError("Recipe target/QEMU revision or architecture mismatch")
        image, expected = prior["image"], prior["image_id"]
        timeout = prior["timeout_seconds"] if timeout is None else timeout
    timeout = 120 if timeout is None else timeout
    if type(timeout) is not int or not 1 <= timeout <= 600:
        raise WorkflowError("Environment smoke timeout must be 1..600 seconds")
    identity = image_identity(project, image)
    if expected is not None and identity != expected:
        raise WorkflowError("Recipe image changed; choose the current route explicitly")
    from ..platform.service import load, required

    accelerator = load(project)[0]["accelerator"] if required(project) else None
    script = root / "environment-smoke.sh"
    script.write_text(render(root, image, identity, probe.relative_to(root), timeout, accelerator))
    script.chmod(0o755)
    value = {
        "schema_version": 1,
        "binding": binding(project),
        "image": image,
        "image_id": identity,
        "timeout_seconds": timeout,
        "probe": str(probe.relative_to(root)),
        "script_sha256": file_sha256(script),
        "authority": "recipe only; run current assertions",
        "accelerator": accelerator,
    }
    (root / NAME).write_text(json.dumps(value, indent=2) + "\n")
    return {
        "script": str(script),
        "recipe": str(root / NAME),
        "probe": str(probe),
        "status": "PREPARED_NOT_EXECUTED",
    }


def inputs(project, script):
    path = script.parent / NAME
    if not path.is_file():
        return None
    value = read_recipe(path)
    if (
        value.get("schema_version") != 1
        or value.get("binding") != binding(project)
        or value.get("script_sha256") != file_sha256(script)
    ):
        raise WorkflowError(
            "Environment recipe or generated wrapper changed; regenerate explicitly"
        )
    if not isinstance(value.get("probe"), str):
        raise WorkflowError("Environment recipe requires a probe path")
    from ..platform.service import load, required

    if required(project) and value.get("accelerator") != load(project)[0]["accelerator"]:
        raise WorkflowError("Prepared probe accelerator differs from the selected platform route")
    probe = (script.parent / value["probe"]).resolve()
    if script.parent not in probe.parents or not probe.is_file():
        raise WorkflowError("Recipe probe is missing or outside the environment workspace")
    return {
        "recipe_sha256": file_sha256(path),
        "probe_sha256": file_sha256(probe),
        "image_id": value["image_id"],
        "image": value["image"],
        "timeout_seconds": value["timeout_seconds"],
        "accelerator": value.get("accelerator"),
    }


def archive(script, destination):
    """Retain the exact template and self-contained probe alongside this attempt."""
    path = script.parent / NAME
    value = read_recipe(path)
    for source, name in (
        (path, NAME),
        (script, "environment-smoke.sh"),
        (script.parent / value["probe"], "probe.sh"),
    ):
        (destination / name).write_bytes(source.read_bytes())


def command(args):
    from ..composition import open_project

    project = open_project(Path(args.path))
    print(
        json.dumps(
            prepare(
                project,
                Path(args.probe),
                image=args.image,
                recipe=args.recipe,
                timeout=args.timeout,
            ),
            ensure_ascii=False,
        )
    )


def register(commands):
    parser = commands.add_parser(
        "prepare-smoke", help="generate a bound Docker wrapper; no execution"
    )
    parser.add_argument("path")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--image")
    source.add_argument("--recipe", type=Path)
    parser.add_argument("--probe", required=True)
    parser.add_argument("--timeout", type=int)
    parser.set_defaults(handler=command)
