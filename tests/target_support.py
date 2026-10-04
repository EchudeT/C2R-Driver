from driver_port_factory.target_study.contracts import TargetStudyStage
from driver_port_factory.target_study.service import TargetStudyService


def accept_target_study(project):
    project.start(TargetStudyStage.STUDY)
    report = project.root / "target-profile.md"
    report.write_text(
        "# Fixture target study\nInspect pinned registration, ownership and packaging originals.\n"
    )
    write_probe_fixture(project, report)
    TargetStudyService().accept(project, report)


def write_probe_fixture(project, report):
    """Synthetic topic interpretations only; no claim about an actual driver."""
    write_route_fixture(report)
    import json

    from driver_port_factory.knowledge.contracts import KnowledgeDomain
    from driver_port_factory.knowledge.index import KnowledgeIndex
    from driver_port_factory.knowledge.probes import TARGET_TOPICS

    index = KnowledgeIndex.for_project(project)
    hit = index.search("registration", domain=KnowledgeDomain.TARGET)["results"][0]
    rows = [
        {
            "topic": topic,
            "query": "registration",
            "applicability": "APPLICABLE",
            "interpretation": "Synthetic fixture; semantic relevance is not established.",
            "evidence": [
                {
                    "chunk_id": hit["chunk_id"],
                    "line_start": hit["line_start"],
                    "line_end": hit["line_end"],
                }
            ],
        }
        for topic in TARGET_TOPICS
    ]
    report.with_suffix(".probes.json").write_text(json.dumps({"probes": rows}))


def write_route_fixture(report):
    """Synthetic reference graph, not a real driver design or observation."""
    import json

    suffix = """
## Fixture route
Use the fixture registration path; it is not a real kernel capability claim.
## Fixture init
Initialize the synthetic device and preserve its cleanup.
## Fixture operation
Expose the synthetic operation after initialization.
## Fixture contract
Preserve example_init returning shared_value; compare the returned value.
"""
    if "## Fixture route\n" not in report.read_text():
        report.write_text(report.read_text() + suffix)
    index = {
        "main_route": [{"id": "R1", "section": "Fixture route"}],
        "behaviors": [
            {
                "id": "init",
                "section": "Fixture init",
                "route": ["R1"],
                "contracts": ["C1"],
                "depends_on": [],
            },
            {
                "id": "operation",
                "section": "Fixture operation",
                "route": ["R1"],
                "contracts": ["C1"],
                "depends_on": ["init"],
            },
        ],
        "contracts": [
            {
                "id": "C1",
                "section": "Fixture contract",
                "sources": [
                    {
                        "repository": "source",
                        "path": "drivers/example.c",
                        "line_start": 1,
                        "line_end": 3,
                    }
                ],
            }
        ],
        "premises": [],
        "learn": [],
    }
    report.with_suffix(".route.json").write_text(json.dumps(index))
    return index
