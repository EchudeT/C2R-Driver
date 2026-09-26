"""Context policy controls and read-only experiment reports."""

import json
from pathlib import Path

from ..composition import open_project
from ..control.context_report import compare_reports, context_report
from ..control.runtime import controller_run
from ..core.models import WorkflowError
from .context_policy import POLICIES, configure_policy, read_policy


def command_policy(arguments):
    project = open_project(Path(arguments.path))
    if arguments.policy is None:
        result = read_policy(project)
    else:
        with controller_run(project):
            result = configure_policy(project, arguments.policy, reason=arguments.reason)
    print(json.dumps(result, ensure_ascii=False, indent=2))


def command_report(arguments):
    report = context_report(open_project(Path(arguments.path)), stage=arguments.stage)
    if arguments.compare:
        other = context_report(open_project(Path(arguments.compare)), stage=arguments.stage)
        report = compare_reports(report, other)
    print(json.dumps(report, ensure_ascii=False, indent=2))


def command_logs(arguments):
    from .context_logs import log_report
    print(json.dumps(log_report(open_project(Path(arguments.path), read_only=True)),
                     ensure_ascii=False, indent=2))


def command_export(arguments):
    from .context_logs import export_rollout
    try:
        result = export_rollout(open_project(Path(arguments.path), read_only=True),
                                arguments.job_id, Path(arguments.destination),
                                index=arguments.snapshot)
    except (OSError, ValueError, IndexError, KeyError, TypeError) as error:
        raise WorkflowError(f"context log export failed: {error}") from error
    print(json.dumps(result, indent=2))


def register_context_commands(commands):
    logs = commands.add_parser("context-logs", help="audit retained native context logs read-only")
    logs.add_argument("path")
    logs.set_defaults(handler=command_logs)
    export = commands.add_parser("context-log-export", help="reconstruct a retained native rollout")
    export.add_argument("path")
    export.add_argument("job_id")
    export.add_argument("destination")
    export.add_argument("--snapshot", type=int, default=-1, help="snapshot index; default latest")
    export.set_defaults(handler=command_export)
    policy = commands.add_parser(
        "context-policy", help="inspect or set an optional context strategy")
    policy.add_argument("path")
    policy.add_argument("--policy", choices=POLICIES)
    policy.add_argument("--reason", default="operator selected context policy")
    policy.set_defaults(handler=command_policy)

    report = commands.add_parser(
        "context-report", help="report context costs and evidence read-only")
    report.add_argument("path")
    report.add_argument("--compare", help="second run; produces a descriptive comparison")
    report.add_argument("--stage", help="restrict call and continuation statistics to one stage")
    report.set_defaults(handler=command_report)
