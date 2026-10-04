"""Project binding and platform evidence; model reports cannot mark a route verified."""

import fcntl
import json
import os
import shlex
import shutil
import sys
import uuid
from contextlib import contextmanager
from pathlib import Path

from ..acquisition.repository import load_repository_acquisition
from ..core.models import ActorRole, FileArtifact, StageStatus, WorkflowError
from ..environment.contracts import EnvironmentArtifact as A
from ..environment.contracts import EnvironmentStage as S
from ..environment.smoke_recipe import binding, image_identity
from ..knowledge.index import file_sha256
from ..migration.implementation import worktree_files
from . import executor
from .configuration import describe, selected
from .profile import adapter_identity, asterinas, digest


def required(project):
    return (
        project.config.managed_platform
        and project.config.target_platform.strip().lower() == "asterinas"
    )


def location(project):
    target = load_repository_acquisition(project).target_worktree
    return project.root / target.path, target.base_commit


def root(project):
    return location(project)[0] / ".dpf-output/platform"


@contextmanager
def locked(project):
    directory = root(project)
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / ".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield directory


def active(project, *, environment=False):
    project.ensure_role(ActorRole.DEVELOPER, ActorRole.MIGRATION_OPERATOR)
    allowed = (
        {S.RECOVERY.value}
        if environment
        else {
            S.RECOVERY.value,
            "target_platform_study",
            "migration_contracts",
            "target_framework_enablement",
            "driver_implementation",
            "artifact_preparation",
            "public_qemu_validation",
        }
    )
    if not any(
        s.name.value in allowed and s.status is StageStatus.RUNNING for s in project.stages()
    ):
        raise WorkflowError("Platform execution requires an active environment or worker stage")


def latest(project, kind):
    refs = [r for r in project.current_artifact_refs(stage=S.RECOVERY) if r.kind == kind.value]
    if not refs:
        raise WorkflowError(f"Missing platform evidence: {kind.value}")
    return json.loads(project.artifacts.read(max(refs, key=lambda r: r.ordinal)))


def load(project):
    profile = latest(project, A.PLATFORM_PROFILE)
    if (profile["image"], profile["accelerator"]) != selected(project.config):
        raise WorkflowError("Platform profile differs from the configured Docker route")
    worktree, revision = location(project)
    if (
        profile["binding"] != binding(project)
        or profile["target_revision"] != revision
        or profile["adapter_identity"] != adapter_identity()
    ):
        raise WorkflowError("Platform revision or adapter changed; revalidate the environment")
    return profile, worktree


def check_image(project, profile):
    if image_identity(project, profile["image"]) != profile["image_id"]:
        raise WorkflowError("Selected platform image changed; no fallback")


def prepare(project):
    active(project, environment=True)
    image, accelerator = selected(project.config)
    worktree, revision = location(project)
    if project.config.target_platform.strip().lower() != "asterinas":
        raise WorkflowError("The built-in platform adapter currently supports Asterinas only")
    # Docker opens the device as its container user, not as the controller user.
    # Host access bits cannot establish container KVM availability; verify boots it.
    if accelerator == "kvm" and not Path("/dev/kvm").is_char_device():
        raise WorkflowError("Selected KVM device is missing; no TCG fallback")
    for relative in ("Makefile", "OSDK.toml", "rust-toolchain.toml", "tools/qemu_args.sh"):
        if not (worktree / relative).is_file():
            raise WorkflowError(f"Platform prerequisite missing: {relative}")
    with locked(project) as directory:
        profile = asterinas(
            image,
            image_identity(project, image),
            revision,
            accelerator,
            machine="pc" if project.config.driver_name == "ne2k-pci" else "q35",
        )
        from .native import configure

        configure(project, worktree, profile)
        profile["binding"] = binding(project)
        profile["cache_key"] = digest({"image": profile["image_id"], "revision": revision})[:24]
        path = directory / f"profile-{uuid.uuid4().hex}.json"
        path.write_text(json.dumps(profile, indent=2) + "\n")
        project.record_artifact(S.RECOVERY, FileArtifact(A.PLATFORM_PROFILE, path))
        return {
            "status": "PREPARED_NOT_VERIFIED",
            "profile": str(path),
            "next": command(project, "verify"),
        }


def command(project, action):
    return [
        sys.executable,
        "-m",
        "driver_port_factory.cli",
        "environment",
        "platform",
        action,
        str(project.root),
    ]


def verified(project):
    profile, _ = load(project)
    receipt = latest(project, A.PLATFORM_VALIDATION)
    if receipt.get("status") != "PASS" or receipt.get("profile") != digest(profile):
        raise WorkflowError("Platform build/boot has no current passing validation")
    for item in receipt["evidence"]:
        path = project.root / item["path"]
        if not path.is_file() or file_sha256(path) != item["sha256"]:
            raise WorkflowError("Platform validation evidence changed or disappeared")
    return receipt


def acceptance_binding(project):
    if not required(project):
        return None
    receipt = verified(project)
    profile, _ = load(project)
    check_image(project, profile)
    return {
        "profile": digest(profile),
        "validation": digest(receipt),
        "scope": "clean baseline build and interactive guest boot",
    }


def route_errors(project, container_summary):
    if not required(project):
        return []
    profile, _ = load(project)
    if not container_summary.get("image_ids"):
        return ["Device probe image was not captured; image mismatch is not established"]
    if set(container_summary["image_ids"]) != {profile["image_id"]}:
        return ["Device probe must execute in the verified platform image; no route substitution"]
    return []


def verify(project):
    active(project, environment=True)
    with locked(project) as directory:
        profile, worktree = load(project)
        check_image(project, profile)
        if worktree_files(worktree, profile["target_revision"]):
            raise WorkflowError("Validate a clean target baseline before implementing the driver")
        attempt = directory / "runs" / uuid.uuid4().hex
        attempt.mkdir(parents=True)
        value = {"status": "FAIL", "profile": digest(profile), "evidence": []}
        try:
            built = executor.build(profile, worktree, attempt / "build", profile["cache_key"])
            nonce = "DPF_BOOT_" + uuid.uuid4().hex
            case = {
                "steps": [
                    {"wait_serial": profile["ready_text"]},
                    {"send_serial": f"printf '%s%s\\n' DPF_BOOT_ {nonce[9:]}\n"},
                    {"wait_serial": nonce},
                ],
                "timeout_seconds": 120,
            }
            if "native" in profile:
                case = {"native_case": "boot"}
            boot = executor.boot(
                profile,
                worktree,
                attempt / "boot",
                Path(built["artifact"]),
                case,
                profile["cache_key"],
            )
            check_image(project, profile)
            if worktree_files(worktree, profile["target_revision"]):
                raise WorkflowError("Baseline sources changed during platform verification")
            value.update(status=boot["status"], build=built, boot=boot)
        except (OSError, WorkflowError) as error:
            value["error"] = str(error)
        finally:
            value["evidence"] = [
                {"path": str(p.relative_to(project.root)), "sha256": file_sha256(p)}
                for p in sorted(attempt.rglob("*"))
                if p.is_file()
            ]
            path = attempt / "validation.json"
            path.write_text(json.dumps(value, indent=2) + "\n")
            project.record_artifact(S.RECOVERY, FileArtifact(A.PLATFORM_VALIDATION, path))
        if value["status"] != "PASS":
            detail = value.get("error") or value.get("boot", {}).get("error") or "boot failed"
            raise WorkflowError(
                f"Platform validation failed: {detail}; inspect {path}. No driver verdict."
            )
        install_entrypoints(project)
        from ..environment.feedback import validation_summary

        return validation_summary(value, path)


def build(project):
    active(project)
    verified(project)
    with locked(project) as directory:
        profile, worktree = load(project)
        check_image(project, profile)
        attempt = directory / "runs" / uuid.uuid4().hex
        (directory / "current-build.json").unlink(missing_ok=True)
        from .dependencies import prepare as prepare_dependencies

        dependencies = prepare_dependencies(profile, worktree, attempt / "dependencies")
        check_image(project, profile)
        files = worktree_files(worktree, profile["target_revision"])
        result = executor.build(profile, worktree, attempt, profile["cache_key"])
        check_image(project, profile)
        if worktree_files(worktree, profile["target_revision"]) != files:
            (attempt / "rejected.json").write_text(
                json.dumps(
                    {
                        "status": "REJECTED",
                        "reason": "Source changed during build",
                        "before": files,
                        "after": worktree_files(worktree, profile["target_revision"]),
                    }
                )
                + "\n"
            )
            raise WorkflowError("Source changed during build; artifact not published")
        artifact = worktree / ".dpf-output/runtime-artifact"
        shutil.copyfile(result["artifact"], artifact)
        result.update(
            files=files,
            published_sha256=file_sha256(artifact),
            dependencies=dependencies,
            receipt=str(attempt / "build.json"),
        )
        (directory / "current-build.json").write_text(json.dumps(result, indent=2) + "\n")
        return result


def presence(project):
    verified(project)
    profile, worktree = load(project)
    artifact = Path(
        os.environ.get("DPF_RUNTIME_ARTIFACT", str(worktree / ".dpf-output/runtime-artifact"))
    ).resolve()
    built = json.loads((root(project) / "current-build.json").read_text())
    if (
        built["profile"] != digest(profile)
        or built["files"] != worktree_files(worktree, profile["target_revision"])
        or not artifact.is_relative_to(project.root)
        or not artifact.is_file()
        or built["published_sha256"] != file_sha256(artifact)
    ):
        raise WorkflowError("Build/source/runtime identity mismatch; rebuild current inputs")
    return {"status": "BUILD_IDENTITY_MATCH", "scope": "not driver functional acceptance"}


def run_case(project, case_path):
    active(project)
    verified(project)
    with locked(project) as directory:
        profile, worktree = load(project)
        check_image(project, profile)
        case_path = (worktree / case_path).resolve()
        if not case_path.is_relative_to(worktree) or not case_path.is_file():
            raise WorkflowError("Case must be a worktree JSON file")
        case = json.loads(case_path.read_text())
        from .guest import validate_case

        if "native" in profile:
            from .native import validate_case as validate_native_case

            validate_native_case(profile, case)
        else:
            validate_case(case)
        artifact = Path(
            os.environ.get("DPF_RUNTIME_ARTIFACT", str(worktree / ".dpf-output/runtime-artifact"))
        ).resolve()
        if not artifact.is_relative_to(project.root) or not artifact.is_file():
            raise WorkflowError("Runtime must be a project artifact")
        built = json.loads((directory / "current-build.json").read_text())
        files = worktree_files(worktree, profile["target_revision"])
        if (
            built["profile"] != digest(profile)
            or built["files"] != files
            or built["published_sha256"] != file_sha256(artifact)
        ):
            raise WorkflowError("Source/profile/runtime changed; build current inputs first")
        attempt = worktree / ".dpf-output/qemu-runs" / uuid.uuid4().hex
        attempt.mkdir(parents=True)
        result = executor.boot(profile, worktree, attempt, artifact, case, profile["cache_key"])
        check_image(project, profile)
        if (
            worktree_files(worktree, profile["target_revision"]) != files
            or file_sha256(artifact) != built["published_sha256"]
        ):
            raise WorkflowError("Inputs changed during runtime check")
        from .log_checks import capture

        captured = capture(
            project, attempt, result, profile, files, built["published_sha256"], case
        )
        if result["status"] != "PASS":
            raise WorkflowError(
                f"Platform/case failed: {result.get('error', 'inspect execution logs')}\n"
                f"Boot-log capture={captured}; original run stays FAIL. "
                f"Inspect {attempt / 'boot.json'}"
            )
        return {
            "status": "CASE_OBSERVED",
            "receipt": str(attempt / "boot.json"),
            "capture": captured,
        }


def install_entrypoints(project):
    from .public_tests import install as install_public_tests
    from .suite import install

    install(location(project)[0])
    install_public_tests(project, location(project)[0])
    directory = location(project)[0] / ".dpf-output/harness/platform"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "binding.json").write_text(json.dumps(load(project)[0], indent=2) + "\n")
    for name, action in (
        ("build.sh", "build"),
        ("format.sh", "format"),
        ("run-case.sh", "run-case"),
    ):
        text = "#!/bin/sh\nset -eu\nexec " + shlex.join(command(project, action))
        (directory / name).write_text(text + ' "$@"\n')
        (directory / name).chmod(0o755)
    presence_script = location(project)[0] / ".dpf-output/check-presence.sh"
    if not presence_script.exists():
        presence_script.write_text(
            "#!/bin/sh\nset -eu\nexec " + shlex.join(command(project, "presence")) + "\n"
        )
        presence_script.chmod(0o755)


def context(project):
    if not required(project):
        return {}
    value = {
        "required": True,
        "configured_route": describe(project.config),
        "prepare": command(project, "prepare"),
        "verify": command(project, "verify"),
        "scope": "baseline build/boot and transport, not migrated-driver acceptance",
    }
    try:
        verified(project)
        profile, _ = load(project)
        value.update(
            status="VERIFIED",
            route={
                "image": profile["image"],
                "accelerator": profile["accelerator"],
                "qemu_args": profile["qemu_args"],
                "build_argv": profile["build_argv"],
                "limits": "Baseline boot does not establish device BAR assignment or driver "
                "integration. Check the selected device path before adapting the framework. "
                "The route is fixed; do not substitute firmware or acceleration.",
            },
            build=command(project, "build"),
            format=command(project, "format"),
            format_interface="Append --package NAME (repeat for affected packages). Default check; "
            "--write applies formatting in the pinned container. Format before building/testing. "
            "No custom formatter script or host Rust toolchain is needed.",
            run_case=command(project, "run-case"),
            case_interface={
                "path": ".dpf-output/harness/<case>.json",
                "invoke": "Append the case file path to run_case argv. Use it from the managed "
                "implementation-smoke/public-qemu script; no custom QMP transport.",
                "fields": {
                    "devices": "list of QEMU -device argument strings",
                    "timeout_seconds": "integer 1..600, whole-case deadline",
                    "steps": "1..100 ordered single-action objects",
                },
                "steps": {
                    "wait_serial": "literal text (raw stream; may include command echo)",
                    "assert_boot_log": "literal boot message before guest input; controller "
                    "ignores display colors. Prefer this over dmesg/grep for kernel boot logs. "
                    "A missing message fails once boot output ends, without waiting full timeout",
                    "send_serial": "text including newline",
                    "guest_assert": "shell command that exits 0 iff its behavioral assertion "
                    "holds; requires a ready guest shell. Controller checks a fresh "
                    "echo-resistant exit marker; no authored success marker needed",
                    "qmp": {"execute": "command", "arguments": {}},
                    "wait_event": "QMP event name",
                    "expect_event": {"event": "QMP event name", "data": {"<field>": "<expected>"}},
                    "qmp_assert": {
                        "execute": "query command",
                        "arguments": {},
                        "match": {"<returned-field>": "<expected>"},
                    },
                    "observe_seconds": "positive seconds, at most timeout_seconds",
                    "assert_no_event": "QMP event name",
                    "assert_event_counts": "object mapping event names to exact cumulative counts; "
                    "use observe_seconds first when checking an absence window",
                },
                "example": {"devices": [], "timeout_seconds": 60, "steps": [{"wait_serial": "# "}]},
                "limits": "Example is only a shell readiness check. Supply the selected device "
                "and actual required assertions. QMP command success does not assert "
                "its return value; use qmp_assert to match explicit returned fields, expect_event "
                "to match event data. Object matches are subsets; scalars/lists match exactly. "
                "For shell tests use guest_assert; send_serial followed by "
                "a text marker may match terminal command echo. wait_serial is a low-level "
                "literal observation, not a shell exit-code assertion. "
                "Complete raw results remain in the returned logs.",
            },
        )
        if "native" in profile:
            value["route"].pop("qemu_args", None)
            value["route"]["qemu_args_source"] = "frozen tools/qemu_args.sh normal"
            value["case_interface"] = {
                "path": ".dpf-output/harness/public/native-*.json",
                "invoke": "Use the installed case IDs with driver_checks.check. "
                "All test stimuli and assertions are already provided; do not author cases.",
            }
    except (WorkflowError, OSError, KeyError):
        value["status"] = "NOT_VERIFIED"
    return value
