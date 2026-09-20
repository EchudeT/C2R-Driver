import json
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch

import ijson
import pytest

from driver_port_factory.cli import main
from driver_port_factory.core.artifacts import ArtifactStore
from driver_port_factory.core.models import WorkflowError
from driver_port_factory.knowledge.index import file_sha256
from driver_port_factory.source_analysis.navigation import (
    cache_root,
    facts_ref,
    open_navigation,
    prepare_navigation,
)
from driver_port_factory.source_analysis.query import query_symbols
from tests.test_workflow_alignment import ready_implementation


def test_cli_before_analysis_reports_phase_boundary_without_creating_cache(tmp_path, capsys):
    from driver_port_factory.core.models import StageStatus
    from driver_port_factory.migration.contracts import MigrationStage as M
    from driver_port_factory.source_analysis.contracts import SourceAnalysisStage as C

    project = ready_implementation(tmp_path)
    project.start(M.DRIVER_IMPLEMENTATION)
    project.retry_from(C.SOURCE_CLOSURE,
                       trigger=M.DRIVER_IMPLEMENTATION,
                       reason="test phase boundary")
    assert project.stage(C.SOURCE_CLOSURE).status is StageStatus.READY
    before = (cache_root(project) / "manifest.json").read_bytes()
    for command in (["c-facts", "--symbol", "example_init"], ["c-facts-build"]):
        assert main(["knowledge", command[0], str(project.root), *command[1:]]) == 2
        assert "SOURCE_INPUTS_REQUIRED" in capsys.readouterr().err
        assert (cache_root(project) / "manifest.json").read_bytes() == before


def reference_queries(project, symbols, *, source_path=None, limit=5):  # noqa: C901, PLR0912
    """Independent linear JSON oracle preserving the pre-SQLite query semantics."""
    facts = json.loads(project.artifacts.read(facts_ref(project)))
    results = {symbol: [] for symbol in symbols}
    counts = Counter()
    all_identities = {}
    for unit in facts["units"]:
        path = project.artifacts.path_for_digest(unit["semantic_index"]["sha256"])
        assert file_sha256(path) == unit["semantic_index"]["sha256"]
        with path.open("rb") as stream:
            indexes = next(ijson.items(stream, "indexes"))
        all_identities.update({item["node_id"]: item
                               for item in indexes["definition_identities"]["functions"]})
        names = {
            item["node_id"]: item["name"] for item in indexes["definition_identities"]["functions"]
        }
        names.update({item["id"]: item.get("name") for item in indexes["external_declarations"]})
        pending_results = []
        for category, identities in indexes["definition_identities"].items():
            for identity in identities:
                symbol = identity.get("name")
                if symbol not in results or (
                    source_path
                    and not any(
                        item.get("path", "").startswith(source_path)
                        for item in identity.get("closure_source", [])
                    )
                ):
                    continue
                counts[symbol] += 1
                if len(results[symbol]) >= limit:
                    continue
                node = identity["node_id"]
                result = {
                    "unit_id": unit["unit_id"],
                    "category": category,
                    "identity": identity,
                    "semantic_index": str(path),
                }
                if category == "records":
                    result["layouts"] = [
                        item
                        for item in unit["raw_facts"]["record_layout"]["summary"]["records"]
                        if item["ast_node_id"] == node
                    ]
                else:
                    calls = [
                        call for call in indexes["calls"] if call["node_id"].startswith(node + ".")
                    ]
                    result.update(
                        {
                            "call_count": len(calls),
                            "calls_truncated": len(calls) > 20,
                            "calls": [
                                {**call, "target_name": names.get(call.get("target_id"))}
                                for call in calls[:20]
                            ],
                            "effect_counts": dict(
                                Counter(
                                    effect["kind"]
                                    for effect in indexes["effects"]
                                    if effect["node_id"].startswith(node + ".")
                                )
                            ),
                            "cfg_gaps": [
                                item
                                for item in unit["raw_facts"]["cfg"]["summary"].get(
                                    "unavailable_functions", []
                                )
                                if item["ast_node_id"] == node
                            ],
                        }
                    )
                results[symbol].append(result)
                pending_results.append(result)
        pending = {
            call["target_id"]
            for result in pending_results
            for call in result.get("calls", [])
            if call.get("target_id") and not call.get("target_name")
        }
        resolved = {}
        if pending:
            with path.open("rb") as stream:
                for node in ijson.items(stream, "nodes.item"):
                    if node["id"] in pending:
                        resolved[node["id"]] = node.get("name")
                        pending.remove(node["id"])
                        if not pending:
                            break
        for result in pending_results:
            for call in result.get("calls", []):
                if not call.get("target_name"):
                    call["target_name"] = resolved.get(call.get("target_id"))
    for items in results.values():
        for result in items:
            for call in result.get("calls", []):
                if "candidate_target_ids" in call:
                    call["candidate_targets"] = [
                        {"node_id": target, "name": all_identities.get(target, {}).get("name"),
                         "source_location": all_identities.get(target, {}).get("source_location")}
                        for target in call["candidate_target_ids"]
                    ]
    return [
        {
            "symbol": symbol,
            "match_count": counts[symbol],
            "truncated": counts[symbol] > len(results[symbol]),
            "results": results[symbol],
            "scope": "Structured navigation only; inspect cited originals for behavior and safety.",
        }
        for symbol in dict.fromkeys(symbols)
    ]


def test_index_matches_linear_oracle_and_warm_queries_never_parse_ast(tmp_path):
    project = ready_implementation(tmp_path)
    facts = json.loads(project.artifacts.read(facts_ref(project)))
    symbols = {"missing"}
    for unit in facts["units"]:
        semantic = json.loads(
            project.artifacts.path_for_digest(unit["semantic_index"]["sha256"]).read_text()
        )
        symbols.update(
            identity["name"]
            for identities in semantic["indexes"]["definition_identities"].values()
            for identity in identities
        )
    symbols = sorted(symbols)
    for offset in range(0, len(symbols), 32):
        selected = symbols[offset : offset + 32]
        for source_path in (None, "drivers/", "not-present/"):
            for limit in (1, 5):
                expected = reference_queries(
                    project, selected, source_path=source_path, limit=limit
                )
                with (
                    patch(
                        "driver_port_factory.source_analysis.navigation.ijson.items",
                        side_effect=AssertionError("warm query parsed AST"),
                    ),
                    patch.object(
                        project,
                        "load_json_artifact",
                        side_effect=AssertionError("warm query loaded full facts"),
                    ),
                ):
                    assert (
                        query_symbols(
                            project, symbols=selected, source_path=source_path, limit=limit
                        )
                        == expected
                    )


def test_corrupt_or_stale_cache_is_rebuilt_but_corrupt_evidence_is_rejected(tmp_path):
    project = ready_implementation(tmp_path)
    root = cache_root(project)
    expected = query_symbols(project, symbols=["example_init"])
    for filename in ("symbols.sqlite", "manifest.json"):
        (root / filename).write_text("broken")
        with pytest.raises(WorkflowError, match="c-facts-build"):
            query_symbols(project, symbols=["example_init"])
        prepare_navigation(project)
        assert query_symbols(project, symbols=["example_init"]) == expected
    ref = facts_ref(project)
    facts = json.loads(project.artifacts.read(ref))
    new = project.artifacts.put_bytes(
        json.dumps({**facts, "test_revision": 2}).encode(), kind=ref.kind
    )
    changed = replace(ref, content=new)
    with patch.object(project, "artifact", return_value=changed):
        with pytest.raises(WorkflowError, match="stale"):
            query_symbols(project, symbols=["example_init"])
        prepare_navigation(project)
        assert query_symbols(project, symbols=["example_init"]) == expected
        semantic = project.artifacts.path_for_digest(facts["units"][0]["semantic_index"]["sha256"])
        semantic.write_text("broken evidence")
        with pytest.raises(WorkflowError, match="integrity"):
            prepare_navigation(project)
        with pytest.raises(WorkflowError, match="integrity"):
            query_symbols(project, symbols=["example_init"])


def test_cli_build_is_explicit_and_query_does_not_write(tmp_path, capsys):
    project = ready_implementation(tmp_path)
    manifest = cache_root(project) / "manifest.json"
    manifest.unlink()
    command = ["knowledge", "c-facts", str(project.root), "--symbol", "example_init"]
    assert main(command) == 2
    assert not manifest.exists()
    assert "c-facts-build" in capsys.readouterr().err
    assert main(["knowledge", "c-facts-build", str(project.root)]) == 0
    capsys.readouterr()
    before = {
        path.name: (path.stat().st_mtime_ns, path.read_bytes())
        for path in cache_root(project).iterdir()
    }
    assert main(command) == 0
    assert before == {
        path.name: (path.stat().st_mtime_ns, path.read_bytes())
        for path in cache_root(project).iterdir()
    }


def test_ambiguous_symbols_layouts_fallback_names_and_call_truncation(tmp_path):
    artifacts = ArtifactStore(tmp_path / "cas")
    identity = {
        "name": "entry",
        "node_id": "tu.1",
        "closure_source": [{"path": "drivers/device.c"}],
    }
    record = {"name": "device", "node_id": "tu.2", "closure_source": []}
    semantic = {
        "nodes": [{"id": "indirect", "name": "resolved_from_ast"}],
        "indexes": {
            "definition_identities": {"functions": [identity], "records": [record]},
            "external_declarations": [{"id": "external", "name": "external_call"}],
            "calls": [
                {"node_id": f"tu.1.{index}", "target_id": target}
                for index, target in enumerate(["indirect", "external", None] * 9)
            ]
            + [{"node_id": "tu.10.1", "target_id": "external"}],
            "effects": [{"node_id": "tu.1.2", "kind": "write"}] * 3
            + [{"node_id": "tu.10.1", "kind": "read"}],
        },
    }
    blob = artifacts.put_bytes(json.dumps(semantic).encode(), kind="semantic")
    units = [
        {
            "unit_id": name,
            "semantic_index": {"sha256": blob.digest},
            "raw_facts": {
                "cfg": {
                    "summary": {"unavailable_functions": [{"ast_node_id": "tu.1", "why": "gap"}]}
                },
                "record_layout": {"summary": {"records": [{"ast_node_id": "tu.2", "size": 8}]}},
            },
        }
        for name in ("first", "second")
    ]
    ref = artifacts.put_bytes(json.dumps({"units": units}).encode(), kind="facts")
    project = SimpleNamespace(
        stage=lambda _: SimpleNamespace(status=__import__("driver_port_factory.core.models", fromlist=["StageStatus"]).StageStatus.PASS),
        control=tmp_path / "control",
        artifacts=artifacts,
        artifact=lambda *_: ref,
        load_json_artifact=lambda *_: json.loads(artifacts.read(ref)),
    )
    prepare_navigation(project)
    for limit in (1, 5):
        for prefix in (None, "drivers/", "other/"):
            symbols = ["entry", "device", "missing", "entry"]
            assert query_symbols(project, symbols=symbols, limit=limit, source_path=prefix) == (
                reference_queries(project, symbols, limit=limit, source_path=prefix)
            )
    result = query_symbols(project, symbols=["entry"], limit=1)[0]
    assert result["match_count"] == 2 and result["truncated"]
    detail = result["results"][0]
    assert detail["call_count"] == 27 and detail["calls_truncated"]
    assert detail["calls"][0]["target_name"] == "resolved_from_ast"
    assert detail["effect_counts"] == {"write": 3}
    expanded = query_symbols(project, symbols=["entry"], limit=1, detail="calls")[0]["results"][0]
    assert len(expanded["calls"]) == 27 and not expanded["calls_truncated"]


def test_requested_cfg_is_bound_to_the_selected_function(tmp_path):
    project = ready_implementation(tmp_path)
    summary = query_symbols(project, symbols=["example_init"])[0]["results"][0]
    detailed = query_symbols(project, symbols=["example_init"], detail="cfg")[0]["results"][0]
    assert "cfg" not in summary
    assert detailed["cfg"]
    assert all(item["ast_node_id"] == detailed["identity"]["node_id"] for item in detailed["cfg"])


def test_interrupted_build_never_publishes_partial_database(tmp_path):
    project = ready_implementation(tmp_path)
    root = cache_root(project)
    original = (root / "symbols.sqlite").read_bytes()
    manifest = root / "manifest.json"
    manifest.write_text("stale")
    with (
        patch(
            "driver_port_factory.source_analysis.navigation._build",
            side_effect=RuntimeError("interrupted"),
        ),
        pytest.raises(RuntimeError, match="interrupted"),
    ):
        prepare_navigation(project)
    assert (root / "symbols.sqlite").read_bytes() == original
    assert not list(root.glob("build-*"))
    with pytest.raises(WorkflowError, match="c-facts-build"):
        query_symbols(project, symbols=["example_init"])
    prepare_navigation(project)
    assert query_symbols(project, symbols=["example_init"])[0]["match_count"] > 0


def test_concurrent_rebuilds_reuse_one_index_and_open_reader_survives(tmp_path):
    from driver_port_factory.source_analysis.navigation import _build

    project = ready_implementation(tmp_path)
    with closing(open_navigation(project)) as reader:
        expected = reader.execute("SELECT * FROM definitions ORDER BY rowid").fetchall()
        (cache_root(project) / "manifest.json").write_text("stale")
        with (
            patch("driver_port_factory.source_analysis.navigation._build", wraps=_build) as build,
            ThreadPoolExecutor(max_workers=2) as pool,
        ):
            jobs = [pool.submit(prepare_navigation, project) for _ in range(2)]
            for job in jobs:
                job.result(timeout=15)
        assert build.call_count == 1
        assert reader.execute("SELECT * FROM definitions ORDER BY rowid").fetchall() == expected
    assert query_symbols(project, symbols=["example_init"])[0]["match_count"] > 0
