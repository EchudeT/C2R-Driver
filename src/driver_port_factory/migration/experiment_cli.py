"""Direct worker access to authoritative execution without ending the conversation."""
import json
from pathlib import Path

from ..core.models import StageStatus, WorkflowError
from ..composition import open_project
from ..acquisition.repository import load_repository_acquisition
from ..cli_support import command_registry
from .experiments import execute, run_cases


def command_run(args):
    project = open_project(Path(args.path), read_only=True, verify_artifacts=False)
    writable = {'target_framework_enablement', 'driver_implementation',
                'artifact_preparation', 'public_qemu_validation'}
    if not any(s.status is StageStatus.RUNNING and s.name.value in writable for s in project.stages()):
        raise WorkflowError('Managed experiments require an active delivery worker stage')
    worktree = project.root / load_repository_acquisition(project).target_worktree.path
    runtime = (worktree / (args.runtime or '.dpf-output/runtime-artifact')).resolve()
    if project.root not in runtime.parents or not runtime.is_file():
        raise WorkflowError('Experiment runtime must be a regular project file')
    if args.suite:
        results = run_cases(project, worktree, runtime, force=args.fresh)
        if not results:
            raise WorkflowError('No .dpf-output/experiments.json case manifest')
    else:
        script = (worktree / args.script).resolve()
        if worktree not in script.parents or not script.is_file():
            raise WorkflowError('Experiment script must be a regular worktree file')
        result = execute(project, worktree=worktree, script_path=script, runtime_path=runtime,
                         timeout_seconds=args.timeout, force=args.fresh, case_id=args.case)
        results = [{'id': args.case or script.name, 'status': 'PASS' if result.passed else 'FAIL',
                    'observation': result}]
    from .experiment_ack import record_seen
    record_seen(project, args.job_id, results)
    from .diagnostics import summarize
    print(json.dumps([{'feedback': summarize(r['observation'].command), 'id': r['id'], 'status': r['status'],
                      'receipt': str(r['observation'].trace_path.parent / 'receipt.json'),
                      'logs': list(r['observation'].logs)} for r in results], indent=2))


def register_commands(commands):
    root = commands.add_parser('experiment', help='controller-recorded experiments available inside a worker task')
    subs = command_registry(root, dest='experiment_command')
    run = subs.add_parser('run')
    run.add_argument('path')
    mode = run.add_mutually_exclusive_group(required=True)
    mode.add_argument('--script')
    mode.add_argument('--suite', action='store_true')
    run.add_argument('--job-id')
    run.add_argument('--runtime')
    run.add_argument('--case')
    run.add_argument('--timeout', type=int, default=300)
    run.add_argument('--fresh', action='store_true', help='request a new independent repetition')
    run.set_defaults(handler=command_run)

    cases = subs.add_parser('generate-cases', help='budgeted public inputs; no model or automatic execution')
    cases.add_argument('path')
    cases.add_argument('--spec', required=True)
    cases.add_argument('--budget', type=int, default=12)
    cases.set_defaults(handler=command_generate_cases)
    probe = subs.add_parser('probe', help='early compile/API or fixed-input differential observation; no stage PASS')
    probe.add_argument('path')
    probe.add_argument('--script', required=True, help='candidate script, relative to run root or absolute')
    probe.add_argument('--reference', help='reference script for exact stdout comparison')
    probe.add_argument('--inputs', help='fixed input file exposed as DPF_PROBE_INPUT')
    probe.add_argument('--timeout', type=int, default=120)
    probe.add_argument('--dependency', action='append', default=[], help='adapter source/helper file to bind and archive')
    probe.add_argument('--contract', action='append', default=[])
    probe.add_argument('--question', help='optional design-changing uncertainty this probe resolves')
    probe.set_defaults(handler=command_probe)

    ack = subs.add_parser('acknowledge', help='explicitly self-review experiments before final submission')
    ack.add_argument('path')
    ack.add_argument('--job-id', required=True)
    ack.add_argument('--report', required=True)
    ack.set_defaults(handler=command_ack)


def command_ack(args):
    from .experiment_ack import acknowledge
    project = open_project(Path(args.path), read_only=True, verify_artifacts=False)
    print(json.dumps({'acknowledgment': str(acknowledge(project, args.job_id, Path(args.report)))}))


def command_probe(args):
    from .probes import run
    project = open_project(Path(args.path), read_only=True, verify_artifacts=False)
    print(json.dumps(run(project, args.script, reference=args.reference, inputs=args.inputs,
                         timeout=args.timeout, contracts=args.contract, dependencies=args.dependency,
                         question=args.question), indent=2))


def command_generate_cases(args):
    from .source_cases import generate
    project = open_project(Path(args.path), read_only=True, verify_artifacts=False)
    print(json.dumps(generate(project, args.spec, budget=args.budget), indent=2))
