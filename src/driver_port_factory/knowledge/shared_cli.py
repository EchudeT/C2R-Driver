"""Operator library maintenance and snapshot-pinned worker retrieval."""

import json
from pathlib import Path

from .shared import add_experience, import_git, search, show
from .shared_binding import project_binding
from .shared_store import Library


def query_project(project, query, *, repository=None, **options):
    if repository is not None:
        from ..acquisition.repository import load_repository_acquisition
        from ..acquisition.repository_role import RepositoryRole
        from ..core.models import WorkflowError

        checkout = load_repository_acquisition(project).checkout(RepositoryRole(repository))
        if options.get("platform") not in (None, checkout.platform) or options.get(
            "revision"
        ) not in (None, checkout.resolved_commit):
            raise WorkflowError("Shared query filters conflict with the frozen repository")
        options.update(platform=checkout.platform, revision=checkout.resolved_commit)
    bound = project_binding(project)
    if not bound:
        return {"status": "NOT_CONFIGURED", "results": []}
    return search(Library(bound["root"]), query, snapshot=bound["snapshot"], **options)


def worker(args):
    from ..composition import open_project

    project = open_project(Path(args.path), read_only=True, verify_artifacts=False)
    options = {
        key: getattr(args, key)
        for key in ("platform", "revision", "domain", "include_other_revisions", "limit", "budget")
    }
    from ..short_refs import emit

    print(
        json.dumps(
            emit(query_project(project, args.query, repository=args.repository, **options)),
            ensure_ascii=False,
            indent=2,
        )
    )


def inspect_reference(args):
    from ..composition import open_project
    from ..short_refs import References, emit

    project = open_project(Path(args.path), read_only=True, verify_artifacts=False)
    ref = References(project.root).get(args.reference)
    bound = project_binding(project)
    library = Library(bound["root"])
    result = show(library, ref["entry"], snapshot=bound["snapshot"])
    originals = []
    for citation in result["entry"].get("evidence", []):
        original = show(library, citation["entry"], snapshot=bound["snapshot"])
        start, end = citation["line_start"], citation["line_end"]
        originals.append(
            {
                "entry": citation["entry"],
                "origin": original["entry"],
                "line_start": start,
                "line_end": end,
                "text": "\n".join(original["text"].splitlines()[start - 1 : end]),
            }
        )
    print(json.dumps(emit({"experience": result, "originals": originals}), ensure_ascii=False))


def learning_status(args):
    from ..composition import open_project
    from ..control.runtime import controller_run
    from ..core.events import RunEvent
    from .learning import events, publish_optional

    project = open_project(
        Path(args.path), read_only=not args.publish, verify_artifacts=args.publish
    )
    if args.publish:
        with controller_run(project):
            print(json.dumps(publish_optional(project), ensure_ascii=False))
        return
    print(
        json.dumps(
            {
                "offered": events(project, RunEvent.KNOWLEDGE_OFFERED),
                "findings": events(project, RunEvent.KNOWLEDGE_FEEDBACK),
                "publication": events(project, RunEvent.KNOWLEDGE_PUBLISHED),
                "adoption": "Inspect references in worker notes/reports; offers are not adoption.",
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def command(args):
    library = Library(args.library)
    action = args.library_action
    if action == "import-git":
        result = import_git(
            library,
            args.repository,
            args.revision,
            args.file,
            domain=args.domain,
            platform=args.platform,
            source_url=args.source_url,
            license_note=args.license_note,
        )
    elif action == "capture":
        from ..composition import open_project
        from .shared_capture import capture

        result = capture(library, open_project(Path(args.workspace), read_only=True))
    elif action == "learn":
        result = add_experience(library, json.loads(Path(args.specification).read_text()))
    elif action == "retire":
        with library.writing():
            result = library.commit(retire=(args.entry, args.reason))
    elif action == "show":
        result = show(library, args.entry, snapshot=args.snapshot)
    elif action == "search":
        result = search(
            library,
            args.query,
            **{
                key: getattr(args, key)
                for key in (
                    "snapshot",
                    "platform",
                    "revision",
                    "domain",
                    "include_other_revisions",
                    "limit",
                    "budget",
                )
            },
        )
    else:
        key, manifest = library.snapshot(args.snapshot)
        counts = {}
        for entry_id in manifest["entries"]:
            entry = show(library, entry_id, snapshot=key)
            label = entry["entry"]["kind"] + ":" + entry["state"]["status"]
            counts[label] = counts.get(label, 0) + 1
        result = {"snapshot": key, "counts": counts, "verified_objects": True}
    print(json.dumps(result, ensure_ascii=False, indent=2))


def search_options(parser):
    parser.add_argument("--query", required=True)
    parser.add_argument("--platform")
    parser.add_argument("--revision")
    parser.add_argument("--domain")
    parser.add_argument("--include-other-revisions", action="store_true")
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--budget", type=int, default=12000)


def register(commands):
    from ..cli_support import command_registry

    inspect = commands.add_parser("shared-show", help="open a short shared experience reference")
    inspect.add_argument("path")
    inspect.add_argument("--reference", required=True)
    inspect.set_defaults(handler=inspect_reference)
    audit = commands.add_parser(
        "learning", help="inspect learning, or retry publication after acceptance"
    )
    audit.add_argument("path")
    audit.add_argument("--publish", action="store_true")
    audit.set_defaults(handler=learning_status)
    query = commands.add_parser("shared-search", help="query the task's pinned shared snapshot")
    query.add_argument("path")
    query.add_argument(
        "--repository",
        choices=("source", "target", "qemu"),
        help="use the task's frozen platform and revision without copying hashes",
    )
    search_options(query)
    query.set_defaults(handler=worker)
    library = commands.add_parser("library", help="maintain a local cross-driver evidence library")
    actions = command_registry(library, dest="library_action")
    for name in ("import-git", "capture", "learn", "retire", "show", "search", "status"):
        parser = actions.add_parser(name)
        parser.add_argument("library")
        parser.set_defaults(handler=command)
        if name in {"search", "show", "status"}:
            parser.add_argument("--snapshot")
        if name == "import-git":
            for field in (
                "repository",
                "revision",
                "domain",
                "platform",
                "source-url",
                "license-note",
            ):
                parser.add_argument("--" + field, required=True)
            parser.add_argument("--file", action="append", required=True)
        elif name == "capture":
            parser.add_argument("workspace")
        elif name == "learn":
            parser.add_argument("specification")
        elif name == "retire":
            parser.add_argument("--entry", required=True)
            parser.add_argument("--reason", required=True)
        elif name == "show":
            parser.add_argument("--entry", required=True)
        elif name == "search":
            search_options(parser)
