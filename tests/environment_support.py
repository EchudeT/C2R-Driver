from __future__ import annotations


import json


import stat


from pathlib import Path


def qemu_fixture(root: Path, *, qmp: bool = True) -> Path:
    executable = root / ("qemu-system-qmp-fixture" if qmp else "qemu-system-marker-fixture")
    behavior = (
        """
import json, os, socket, sys
argument = sys.argv[sys.argv.index('-qmp') + 1]
path = argument.removeprefix('unix:').split(',server=on', 1)[0]
server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
server.bind(path)
server.listen(1)
connection, _ = server.accept()
stream = connection.makefile('rwb', buffering=0)
stream.write(json.dumps({'QMP': {'version': {'qemu': {'major': 9}}}}).encode() + b'\\r\\n')
request = json.loads(stream.readline())
stream.write(json.dumps({'return': {}, 'id': request['id']}).encode() + b'\\r\\n')
stream.readline()
connection.close()
server.close()
os.unlink(path)
"""
        if qmp
        else "print('QMP_READY')\n"
    )
    executable.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        "if '--version' in sys.argv:\n"
        "    print('QEMU emulator version 9.0.0')\n"
        "    raise SystemExit(0)\n" + behavior,
        encoding="utf-8",
    )
    executable.chmod(executable.stat().st_mode | stat.S_IXUSR)
    return executable


def write_plan(root: Path, route_id: str, executable: Path) -> Path:
    from driver_port_factory.composition import open_project
    from driver_port_factory.acquisition.repository import load_repository_acquisition
    from driver_port_factory.acquisition.repository_role import RepositoryRole
    qemu_source = load_repository_acquisition(open_project(root)).checkout(RepositoryRole.QEMU).checkout_path
    path = root / f"{route_id}.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "route_id": route_id,
                "milestone": "EXPERIMENT_READY",
                "purpose": "execute a bounded QEMU device-model smoke",
                "artifact_mode": "direct-device-model",
                "route_kind": "direct-qemu",
                "device_identity": "example-device",
                "topology": "example-bus on machine-none",
                "command": [str(executable), "-machine", "none"],
                "cwd": ".",
                "environment": {},
                "timeout_seconds": 2,
                "accepted_exit_codes": [0],
                "runner_evidence_paths": [qemu_source],
                "relevance_evidence": "frozen QEMU model source",
                "driver_insertion_or_packaging_path": None,
            }
        ),
        encoding="utf-8",
    )
    return path
