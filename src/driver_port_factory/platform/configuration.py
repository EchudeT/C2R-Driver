"""Operator-selected execution route; no model or host-name inference."""

from ..core.models import WorkflowError

OVMF_PATH = "/root/ovmf/release/OVMF.fd"


def validate_selection(image, accelerator):
    if not isinstance(image, str) or not image.strip():
        raise WorkflowError(
            "Managed platform requires --platform-image and --platform-accelerator "
            "at project creation, before any model call. "
            "Existing unconfigured experiments must use their frozen controller."
        )
    if accelerator not in {"kvm", "tcg"}:
        raise WorkflowError("Select --platform-accelerator kvm or tcg explicitly; no inference")
    return image, accelerator


def selected(config):
    return validate_selection(config.platform_image, config.platform_accelerator)


def describe(config):
    image, accelerator = selected(config)
    return {
        "executor": "docker",
        "image": image,
        "accelerator": accelerator,
        "firmware": OVMF_PATH,
        "selection": "operator configuration; never infer from host OS or installed images",
        "kvm_device": "/dev/kvm" if accelerator == "kvm" else None,
    }
