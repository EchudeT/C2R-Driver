import json

from driver_port_factory.core.container_trace import ContainerTrace


def _docker_trace(arguments: str) -> str:
    return (
        '1234 execve("/bin/docker", '
        f'["docker", {arguments}], 0x0) = 0\n'
    )


def test_container_trace_records_missing_workspace_mount(tmp_path):
    trace_path = tmp_path / "execve.log"
    output_path = tmp_path / "container-processes.json"
    trace_path.write_text(
        _docker_trace('"run", "--rm", "asterinas/dev:0.18.1-20260901", '
                      '"qemu-system-x86_64", "--version"')
    )

    observer = ContainerTrace(tmp_path / "worktree", output_path)
    assert observer.executions(trace_path) == ()

    evidence = json.loads(output_path.read_text())
    assert evidence["observations"] == []
    assert any("did not bind-mount the current execution workspace" in error
               for error in evidence["errors"])
    assert str((tmp_path / "worktree").resolve()) in "\n".join(evidence["errors"])


def test_container_trace_accepts_volume_and_mount_workspace_binds(tmp_path):
    for suffix, arguments in (
        (
            "volume",
            f'"run", "--rm", "-v", "{tmp_path / "worktree"}:/work:rw", '
            '"asterinas/dev:0.18.1-20260901", "qemu-system-x86_64", "-S"',
        ),
        (
            "mount",
            f'"run", "--rm", "--mount=type=bind,source={tmp_path / "worktree"},target=/work", '
            '"asterinas/dev:0.18.1-20260901", "qemu-system-x86_64", "-S"',
        ),
    ):
        trace_path = tmp_path / f"{suffix}.execve.log"
        output_path = tmp_path / f"{suffix}.container-processes.json"
        trace_path.write_text(_docker_trace(arguments))
        observer = ContainerTrace(tmp_path / "worktree", output_path)
        observer.executions(trace_path)
        evidence = json.loads(output_path.read_text())
        assert evidence["errors"] == []
