"""Debug entrypoint for the same controller-owned capture used at acceptance."""

import shlex
import sys


def render(workspace, image, image_id, probe, timeout, accelerator=None):
    argv = [
        sys.executable,
        "-m",
        "driver_port_factory.environment.managed_smoke",
        "--workspace",
        str(workspace),
        "--image",
        image,
        "--image-id",
        image_id,
        "--probe",
        str(probe),
        "--timeout",
        str(timeout),
    ]
    if accelerator is not None:
        argv.extend(["--accelerator", accelerator])
    return "#!/bin/sh\nset -eu\nexec " + shlex.join(argv) + "\n"
