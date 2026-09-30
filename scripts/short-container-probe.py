#!/usr/bin/env python3
"""Opt-in real Docker/QMP collector regression; no model calls or fixed sleeps."""

import argparse
import json
import shlex
import tempfile
from pathlib import Path

from driver_port_factory.core.container_trace import ContainerTrace
from driver_port_factory.core.execution import CommandRunner, observed_script_command


def probe(image, repeats):
    root = Path(tempfile.mkdtemp(prefix="dpf-short-container-"))
    root.chmod(0o700)
    rows = []
    for index in range(repeats):
        work = root / str(index)
        work.mkdir()
        (work / "qmp.jsonl").write_text('\n'.join(json.dumps({'execute': command}) for command in
                                               ('qmp_capabilities', 'query-pci', 'quit')) + '\n')
        script = work / 'smoke.sh'
        script.write_text(
            '#!/bin/sh\nset -eu\n' + shlex.join([
                'docker', 'run', '--rm', '-i', '--network', 'none', '-v', f'{work}:/work',
                image, '/usr/local/qemu/bin/qemu-system-x86_64', '-machine', 'q35,accel=tcg',
                '-m', '128M', '-nodefaults', '-display', 'none', '-serial', 'none',
                '-monitor', 'none', '-qmp', 'stdio', '-device', 'e1000']) + ' < qmp.jsonl\n')
        host_trace = work / 'host.log'
        command = observed_script_command(script, host_trace)
        with ContainerTrace(work, work / 'containers.json') as observer:
            result = CommandRunner(work / 'commands').run(command, cwd=work, timeout_seconds=30)
        observed = observer.executions(host_trace)
        output = Path(result.stdout_path).read_text()
        rows.append({'exit_code': result.exit_code, 'timed_out': result.timed_out,
                     'qmp_device_observed': '"device": 4110' in output,
                     'bound_qemu_count': len(observed),
                     'elapsed_ms': result.duration_milliseconds})
    return {'root': str(root), 'image': image, 'rows': rows,
            'passed': all(r['exit_code'] == 0 and not r['timed_out'] and
                          r['qmp_device_observed'] and r['bound_qemu_count'] == 1 for r in rows),
            'limitation': 'Model-only collector probe, not migrated-driver validation; '
                          'finite sampling cannot guarantee observing every short process.'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', required=True, help='Already available development image')
    parser.add_argument('--repeats', type=int, default=5)
    args = parser.parse_args()
    if not 1 <= args.repeats <= 20:
        parser.error('repeats must be between 1 and 20')
    report = probe(args.image, args.repeats)
    print(json.dumps(report, indent=2))
    raise SystemExit(0 if report['passed'] else 1)
