import json
from pathlib import Path

from driver_port_factory.core.container_trace import ContainerTrace


def test_observer_requires_new_workspace_container_and_live_qemu(tmp_path, monkeypatch):
    observer = ContainerTrace(tmp_path, tmp_path / "observed.json")
    monkeypatch.setattr(observer, "docker", "/usr/bin/docker")
    observer.before = {"old"}
    calls = []
    def call(*args):
        calls.append(args)
        if args[0] == "ps":
            return "old new foreign"
        if args[0] == "inspect":
            source = str(tmp_path if args[1] == "new" else tmp_path / "other")
            return json.dumps([{"Config": {"Image": "test:image"}, "Image": "sha256:fixed",
                                "Mounts": [{"Type": "bind", "Source": source, "Destination": "/work"}]}])
        observer.stop.set()
        assert args[2:] == ("-eo", "pid,args")
        return "PID COMMAND\n123 qemu-system-x86_64 -cdrom /work/runtime.iso\n"
    monkeypatch.setattr(observer, "_call", call)
    observer._watch()
    assert len(observer.records) == 1
    assert observer.records[0]["container_id"] == "new"
    assert ("inspect", "old") not in calls
    host = tmp_path / "exec.log"
    host.write_text('execve("/usr/bin/docker", ["docker", "run", "test:image"], []) = 0\n')
    result = observer.executions(host)
    assert str(tmp_path / "runtime.iso") in result[0][1]
    host.write_text('execve("/usr/bin/docker", ["docker", "run", "other:image"], []) = 0\n')
    assert not observer.executions(host)
    host.write_text('execve("/usr/bin/echo", ["echo", "run", "test:image"], []) = 0\n')
    assert not observer.executions(host)


def test_unavailable_docker_does_not_invent_execution(tmp_path, monkeypatch):
    observer = ContainerTrace(tmp_path, tmp_path / "observed.json")
    monkeypatch.setattr(observer, "docker", None)
    with observer:
        pass
    assert json.loads(observer.output.read_text())["observations"] == []


def test_drive_artifact_mount_is_mapped_and_exactly_bound(tmp_path):
    from driver_port_factory.migration.public_qemu import runtime_in_qemu_arguments

    runtime = tmp_path / "frozen.iso"
    observer = ContainerTrace(tmp_path, tmp_path / "observed.json")
    observer.records = [{"image": "test:image", "argv": [
        "qemu-system-x86_64", "-drive", "file=/runtime-artifact,media=cdrom,readonly=on"
    ], "mounts": [{"Source": str(runtime), "Destination": "/runtime-artifact"}]}]
    host = tmp_path / "exec.log"
    host.write_text('execve("/bin/docker", ["docker", "run", "test:image"], []) = 0\n')
    line = observer.executions(host)[0][1]
    assert runtime_in_qemu_arguments(line, runtime)
    assert not runtime_in_qemu_arguments(line, tmp_path / "frozen")
    assert not runtime_in_qemu_arguments(line.replace("-drive", "-name"), runtime)
    assert not runtime_in_qemu_arguments(line.replace("frozen.iso", "frozen.iso.other"), runtime)
    assert not runtime_in_qemu_arguments(line.replace("file=", "id="), runtime)
