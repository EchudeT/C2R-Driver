"""Docker access policy for a single worker: only its task and public asset mounts."""

from pathlib import Path

FLAGS = {"--rm", "--pull=never", "-i", "--interactive"}
OPTIONS = {
    "--name",
    "--hostname",
    "--device",
    "--network",
    "-v",
    "--volume",
    "-w",
    "--workdir",
    "-e",
    "--env",
    "--cap-add",
    "--entrypoint",
}
CACHES = {"dpf-native-cargo-registry", "dpf-native-cargo-git"}


def permitted_mount(value, writable, readonly):
    parts = value.split(":")
    if len(parts) not in (2, 3) or not parts[1].startswith("/"):
        return False
    origin = parts[0]
    if origin in CACHES:
        return True
    if not origin.startswith("/"):
        return False
    path = Path(origin).resolve()
    if any(path.is_relative_to(root) for root in writable):
        return True
    return (
        len(parts) == 3
        and parts[2] == "ro"
        and any(path == root or path.is_relative_to(root) for root in readonly)
    )


def validated_run(argv, image, writable, readonly):
    """Return a constrained docker run; never allow daemon administration or host mounts."""
    index, name = 1, None
    while index < len(argv) and argv[index].startswith("-"):
        option = argv[index]
        index += 1
        if option in FLAGS:
            continue
        if option not in OPTIONS or index >= len(argv):
            raise ValueError("Docker option unavailable to workers: " + option)
        value = argv[index]
        index += 1
        if option in {"-v", "--volume"} and not permitted_mount(value, writable, readonly):
            raise ValueError("Docker mount outside the current task/public assets")
        if option == "--device" and value not in {"/dev/kvm", "/dev/net/tun"}:
            raise ValueError("Only frozen guest devices are available")
        if option == "--network" and value not in {"none", "bridge"}:
            raise ValueError("Host/container network namespaces are unavailable")
        if option == "--cap-add" and value != "NET_ADMIN":
            raise ValueError("Only NET_ADMIN for the frozen TAP route is available")
        if option == "--name":
            name = value
    if index >= len(argv) or argv[index] != image:
        raise ValueError("Only the frozen Docker image ID is available")
    return name
