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


def test_events_still_require_live_process_mount_and_host_invocation(tmp_path):
    observer = ContainerTrace(tmp_path, tmp_path / 'observed.json')
    info = {'Mounts': [{'Type': 'bind', 'Source': str(tmp_path), 'Destination': '/work'}],
            'Config': {'Image': 'asterinas/dev:test'}, 'Image': 'sha256:image'}

    class API:
        def inspect(self, identifier):
            return info

        def top(self, identifier):
            return 'PID COMMAND\n123 /usr/bin/qemu-system-x86_64 -kernel /work/kernel'

    observer.api = API()
    observer.before.add('old')
    observer._observe('old')
    assert not observer.records
    observer._observe('new')
    observer._observe('new')
    assert len(observer.records) == 1
    host = tmp_path / 'host.log'
    host.write_text('')
    assert observer.executions(host) == ()
    host.write_text(_docker_trace('"run", "asterinas/dev:test"'))
    assert str(tmp_path / 'kernel') in observer.executions(host)[0][1]
    observer.api.top = lambda _: 'PID COMMAND\n123 sh -c echo qemu-system-x86_64'
    observer._observe('shell-only')
    assert len(observer.records) == 1
    info['Mounts'] = []
    observer._observe('unmounted')
    assert len(observer.records) == 1


def test_event_observation_failure_is_nonblocking(tmp_path):
    observer = ContainerTrace(tmp_path, tmp_path / 'observed.json')
    def fail(*args):
        raise RuntimeError('container exited before inspection')
    observer._call = fail
    observer._observe('already-gone')
    assert not observer.records
    assert observer.errors == ['container exited before inspection']


def test_direct_api_failure_falls_back_once_without_losing_observation(tmp_path):
    observer = ContainerTrace(tmp_path, tmp_path / 'observed.json')
    class API:
        def inspect(self, identifier):
            raise PermissionError('API unavailable')
    observer.api = API()
    info = {'Mounts': [{'Type': 'bind', 'Source': str(tmp_path), 'Destination': '/work'}],
            'Config': {'Image': 'asterinas/dev:test'}, 'Image': 'sha256:image'}
    calls = []
    def cli(*args):
        calls.append(args)
        return (json.dumps([info]) if args[0] == 'inspect' else
                'PID COMMAND\n123 qemu-system-x86_64 -kernel /work/kernel')
    observer._call = cli
    observer._observe('new')
    observer._observe('new')
    assert observer.api is None
    assert len(observer.records) == 1
    assert len(observer.errors) == 1
    assert sum(args[0] == 'inspect' for args in calls) == 1


def test_disappeared_container_does_not_disable_api_or_retry_cli(tmp_path):
    from driver_port_factory.core.docker_inspection import DockerInspectionError
    observer = ContainerTrace(tmp_path, tmp_path / 'observed.json')
    class API:
        def inspect(self, identifier):
            raise DockerInspectionError(404)
    api = API()
    observer.api = api
    def forbidden(*args):
        raise AssertionError('must not repeat a gone-container lookup')
    observer._call = forbidden
    observer._observe('gone')
    assert observer.api is api
    assert not observer.records
