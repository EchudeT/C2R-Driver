import json
from unittest.mock import patch

import pytest

from driver_port_factory.codex.sessions import input_changes
from driver_port_factory.cli import main
from driver_port_factory.core.models import WorkflowError
from driver_port_factory.core.validation import ArtifactInputs, BundleValidationContext
from driver_port_factory.source_analysis.contracts import SourceAnalysisArtifact
from driver_port_factory.knowledge.index import file_sha256
from driver_port_factory.source_analysis.query import query_facts, query_symbols
from tests.test_workflow_alignment import ready_implementation


def test_input_changes_are_per_conversation_and_do_not_remove_requirements():
    original = {"frozen_inputs": {"contract": {
        "kind": "contract", "digest": "one", "path": "/cas/one"
    }}, "controller_feedback": "repair the defect"}
    first, known = input_changes(original, {})
    assert first["input_changes"]["new"] == ["frozen_inputs/contract"]
    second, _ = input_changes(original, known)
    assert second["input_changes"]["unchanged"] == ["frozen_inputs/contract"]
    assert second["frozen_inputs"] == original["frozen_inputs"]
    assert second["controller_feedback"] == "repair the defect"
    assert "input_changes" not in original
    changed = {"frozen_inputs": {"contract": {
        "kind": "contract", "digest": "two", "path": "/cas/two"
    }}}
    assert input_changes(changed, known)[0]["input_changes"]["changed"]
    assert input_changes(original, {})[0]["input_changes"]["new"]


def test_batch_matches_single_queries_and_hashes_each_index_once(tmp_path):
    project = ready_implementation(tmp_path)
    expected = [query_facts(project, symbol=symbol, limit=1)
                for symbol in ("example_init", "shared_value", "missing")]
    with patch("driver_port_factory.source_analysis.navigation.file_sha256",
               wraps=file_sha256) as digest:
        actual = query_symbols(project, symbols=["example_init", "shared_value", "missing",
                                                "example_init"], limit=1)
    assert actual == expected
    assert len(digest.call_args_list) == len({call.args[0] for call in digest.call_args_list})
    assert digest.call_count > 0
    path = digest.call_args_list[-1].args[0]
    path.write_text("corrupted")
    with pytest.raises(WorkflowError, match="integrity"):
        query_symbols(project, symbols=["example_init", "missing"])


def test_bundle_selection_does_not_read_unrequested_ast_payloads(tmp_path):
    project = ready_implementation(tmp_path)
    with patch.object(project.artifacts, "read", wraps=project.artifacts.read) as read:
        inputs = ArtifactInputs(project.artifact_refs(), read)
        context = BundleValidationContext(project.root, (), inputs)
        ref, data = context.one_dependency(SourceAnalysisArtifact.COMPILE_MANIFEST)
        assert data and ref.kind == SourceAnalysisArtifact.COMPILE_MANIFEST.value
        read.assert_called_once_with(ref)


@pytest.mark.parametrize("symbols", [[], [""], ["symbol"] * 33])
def test_batch_size_is_bounded(symbols):
    with pytest.raises(WorkflowError, match="1–32"):
        query_symbols(None, symbols=symbols)


def test_c_facts_cli_accepts_batch_and_keeps_single_result_shape(tmp_path, capsys):
    project = ready_implementation(tmp_path)
    command = ["knowledge", "c-facts", str(project.root)]
    assert main([*command, "--symbol", "example_init"]) == 0
    single = json.loads(capsys.readouterr().out)
    assert single["symbol"] == "example_init"
    assert "queries" not in single
    assert main([*command, "--symbol", "example_init", "--symbol", "missing"]) == 0
    batch = json.loads(capsys.readouterr().out)["queries"]
    assert batch[0] == single
    assert batch[1]["symbol"] == "missing"
    assert batch[1]["match_count"] == 0
    assert main([*command, *(["--symbol", "example_init"] * 33)]) == 2
    assert "1–32" in capsys.readouterr().err
