from unittest.mock import patch
import json

import pytest

from driver_port_factory.core.models import WorkflowError
from driver_port_factory.knowledge.index import KnowledgeIndex


def chunk(identifier, **changes):
    return {"chunk_id": identifier, "record_id": "record-" + identifier,
            "domain": "source", "path": "driver.c", "revision": "frozen",
            "sha256": "content-hash", "source_url": "https://example.invalid/source",
            "line_start": 101, "line_end": 130,
            "text": "header line\n" * 25 + "void release_descriptor(void);\n", **changes}


def test_search_deduplicates_before_limit_and_keeps_original_references():
    rows = [chunk("a"), chunk("b"), chunk("c", path="other.c")]
    index = object.__new__(KnowledgeIndex)
    with patch.object(index, "_load_chunks", return_value=rows):
        result = index.search("release_descriptor", limit=2, compact=True)
        assert result["matched_chunks"] == 3 and result["unique_matches"] == 2
        first, second = result["results"]
        assert first["also_indexed_as"] == [{"record_id": "record-b", "chunk_id": "b"}]
        assert second["path"] == "other.c"
        assert "release_descriptor" in first["summary"]
        assert first["summary_line_start"] <= 126 <= first["summary_line_end"]
        assert "text" not in first
        assert index.show("b")["result"] == rows[1]
        assert "also_indexed_as" not in rows[0]  # Index data is untouched.
        filtered = index.search("release_descriptor", record_id="record-b")
        assert filtered["count"] == 1
        assert filtered["results"][0]["chunk_id"] == "b"
        assert filtered["results"][0]["text"] == rows[1]["text"]


@pytest.mark.parametrize("difference", [
    {"revision": "other"}, {"source_url": "https://other.invalid/source"},
    {"domain": "target"}, {"line_start": 201}, {"authority": "independent"},
])
def test_search_retains_different_origins_and_metadata(difference):
    index = object.__new__(KnowledgeIndex)
    with patch.object(index, "_load_chunks", return_value=[chunk("a"), chunk("b", **difference)]):
        result = index.search("release_descriptor", compact=True)
        assert result["count"] == result["unique_matches"] == 2
        assert all("also_indexed_as" not in r for r in result["results"])


def test_summary_falls_back_to_query_token_and_search_preserves_integrity_failure():
    index = object.__new__(KnowledgeIndex)
    with patch.object(index, "_load_chunks", return_value=[chunk("a")]):
        result = index.search("release_descriptor ownership", compact=True)
        assert "release_descriptor" in result["results"][0]["summary"]
    with patch.object(index, "_load_chunks", side_effect=WorkflowError("hash mismatch")):
        with pytest.raises(WorkflowError, match="hash mismatch"):
            index.search("release_descriptor", compact=True)


@pytest.mark.parametrize("compact", [False, True])
def test_batch_matches_single_queries_without_cross_request_validation_cache(compact):
    index = object.__new__(KnowledgeIndex)
    rows = [chunk("a"), chunk("b"), chunk("c", path="other.c")]
    queries = [{"query": "release_descriptor", "limit": 2},
               {"query": "ownership release_descriptor", "path_prefix": "driver"},
               {"query": "release_descriptor", "record_id": "record-b"},
               {"query": "unknown_symbol"}]
    with patch.object(index, "_load_chunks", return_value=rows) as load:
        singles = [index.search(compact=compact, **q) for q in queries]
        load.reset_mock()
        batch = index.search_many(queries, compact=compact)
        assert load.call_count == 1
        for expected, actual in zip(singles, batch["queries"], strict=True):
            restored = {**{k: v for k, v in actual.items() if k != "hits"}, "results": [
                {**batch["documents"][batch["evidence"][hit["chunk_id"]]["document_id"]],
                 **{k: v for k, v in batch["evidence"][hit["chunk_id"]].items()
                    if k != "document_id"}, **hit} for hit in actual["hits"]]}
            assert restored == expected
        assert len(batch["evidence"]) == 3
        load.side_effect = WorkflowError("content changed")
        with pytest.raises(WorkflowError, match="content changed"):
            index.search_many(queries)
        assert load.call_count == 2


def test_batch_rejects_invalid_options_before_loading_index():
    index = object.__new__(KnowledgeIndex)
    invalid = [[], {}, [{"query": "x", "limit": True}], [{"query": "x", "domain": "invented"}],
               [{"query": "x", "record_id": []}], [{"query": "x", "silent_filter": "oops"}]]
    with patch.object(index, "_load_chunks") as load:
        for queries in invalid:
            with pytest.raises(WorkflowError):
                index.search_many(queries)
        load.assert_not_called()


def test_batch_query_contract_remains_backward_compatible():
    from driver_port_factory.knowledge.validation import _query_contract
    value = {"commands": {k: k for k in ("status", "rebuild", "search", "show")},
             "template_sha256": "fixture", "manifest_sha256": "fixture"}
    _query_contract(json.dumps(value).encode())
    value["commands"]["search_batch"] = "batch"
    _query_contract(json.dumps(value).encode())
    del value["commands"]["show"]
    with pytest.raises(WorkflowError):
        _query_contract(json.dumps(value).encode())
