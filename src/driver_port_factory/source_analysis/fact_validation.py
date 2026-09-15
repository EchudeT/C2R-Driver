from __future__ import annotations

from types import MappingProxyType

from ..core.models import WorkflowError
from ..core.validation import ArtifactValidator, json_object, nonempty
from .contracts import SourceAnalysisArtifact
from .fact_model import (
    FactAvailability,
    RawFactKind,
    SemanticFactDomain,
    StructuredAnalysisStatus,
)


def _mapped_definition_ids(
    summary: dict[str, object],
    collection: str,
) -> list[str]:
    records = summary.get(collection)
    if not isinstance(records, list):
        raise WorkflowError(f"structured C {collection} summary must be a list")
    return [
        str(record["ast_node_id"])
        for record in records
        if isinstance(record, dict) and record.get("ast_node_id")
    ]


def _validate_unit(unit: object, analyzer: dict[str, object]) -> tuple[int, int]:
    if not isinstance(unit, dict):
        raise WorkflowError("structured C unit must be an object")
    raw_facts = unit.get("raw_facts")
    required_raw_facts = {fact_kind.value for fact_kind in RawFactKind}
    if not isinstance(raw_facts, dict) or set(raw_facts) != required_raw_facts:
        raise WorkflowError("structured C unit does not contain every raw fact output")
    counts = _semantic_counts(unit, analyzer)
    _validate_raw_records(raw_facts)
    cfg_blocks = _validate_cfg(raw_facts[RawFactKind.CFG.value]["summary"], counts)
    layout_records = _validate_layout(raw_facts[RawFactKind.RECORD_LAYOUT.value]["summary"], counts)
    _validate_textual_facts(raw_facts, unit)
    _validate_availability(raw_facts)
    return cfg_blocks, layout_records


def _semantic_counts(unit: dict[str, object], analyzer: dict[str, object]) -> dict[str, object]:
    counts = unit.get("semantic_counts")
    if not isinstance(counts, dict) or counts.get("source_spans", 0) < 1:
        raise WorkflowError("structured C unit has no source-spanned semantic nodes")
    if counts.get("direct_calls", 0) + counts.get("indirect_calls", 0) != counts.get("calls"):
        raise WorkflowError("structured C call index is internally inconsistent")
    if counts.get("resolved_indirect_calls", 0) + counts.get(
        "unresolved_indirect_calls", 0
    ) != counts.get("indirect_calls"):
        raise WorkflowError("structured C indirect-call index is internally inconsistent")
    if counts.get("unresolved_indirect_calls", 0) != 0:
        raise WorkflowError("structured C facts cannot pass with unresolved indirect calls")
    if unit.get("verified_target_abi") != analyzer.get("target_abi"):
        raise WorkflowError("structured C unit ABI differs from analyzer ABI")
    if unit.get("analyzer_target_triple") != analyzer.get("observed_target_triple"):
        raise WorkflowError("structured C unit target differs from analyzer target")
    return counts


def _validate_raw_records(raw_facts: dict[str, object]) -> None:
    for raw_fact in raw_facts.values():
        if not isinstance(raw_fact, dict) or len(str(raw_fact.get("sha256", ""))) != 64:
            raise WorkflowError("structured C raw fact lacks an output hash")
        if not isinstance(raw_fact.get("summary"), dict):
            raise WorkflowError("structured C raw fact lacks a parsed summary")
        try:
            FactAvailability(raw_fact["availability"])
        except (KeyError, TypeError, ValueError) as error:
            raise WorkflowError("structured C raw fact has invalid availability") from error


def _validate_cfg(summary: dict[str, object], counts: dict[str, object]) -> int:
    cfg_ids = _mapped_definition_ids(summary, "functions")
    if len(cfg_ids) != counts.get("function_definitions", 0) or len(cfg_ids) != len(set(cfg_ids)):
        raise WorkflowError("structured C CFG is not one-to-one with AST definitions")
    cfg_functions = summary["functions"]
    if summary.get("block_count", 0) != sum(
        len(function.get("blocks", [])) for function in cfg_functions if isinstance(function, dict)
    ):
        raise WorkflowError("structured C CFG summary lacks parsed block topology")
    return int(summary.get("block_count", 0))


def _validate_layout(summary: dict[str, object], counts: dict[str, object]) -> int:
    layout_ids = _mapped_definition_ids(summary, "records")
    if len(layout_ids) != counts.get("record_definitions", 0) or len(layout_ids) != len(
        set(layout_ids)
    ):
        raise WorkflowError("structured C layout is not one-to-one with AST definitions")
    if summary.get("record_dump_count", 0) != len(summary["records"]):
        raise WorkflowError("structured C layout summary lacks parsed record facts")
    return int(summary.get("record_dump_count", 0))


def _validate_textual_facts(raw_facts: dict[str, object], unit: dict[str, object]) -> None:
    preprocessor = raw_facts[RawFactKind.PREPROCESSED_SOURCE.value]
    if preprocessor["summary"].get("line_directive_count", 0) < 1:
        raise WorkflowError("structured C preprocessor facts lack line provenance")
    llvm = raw_facts[RawFactKind.LLVM_IR.value]
    if not llvm["summary"].get("target_triple") or not llvm["summary"].get("target_data_layout"):
        raise WorkflowError("structured C LLVM facts lack target ABI layout")
    if llvm["summary"]["target_triple"] != unit.get("analyzer_target_triple"):
        raise WorkflowError("structured C LLVM target differs from analyzer target")


def _validate_availability(raw_facts: dict[str, object]) -> None:
    expected_availability = {
        RawFactKind.TYPED_AST: {FactAvailability.STRUCTURED},
        RawFactKind.CFG: {FactAvailability.STRUCTURED, FactAvailability.EMPTY_VALID},
        RawFactKind.RECORD_LAYOUT: {
            FactAvailability.STRUCTURED,
            FactAvailability.EMPTY_VALID,
        },
        RawFactKind.PREPROCESSED_SOURCE: {FactAvailability.RAW_VALIDATED},
        RawFactKind.LLVM_IR: {FactAvailability.RAW_VALIDATED},
    }
    for fact_kind, accepted in expected_availability.items():
        if FactAvailability(raw_facts[fact_kind.value]["availability"]) not in accepted:
            raise WorkflowError(f"structured C {fact_kind.value} availability is inconsistent")


def _structured_facts(data: bytes) -> None:
    value = json_object(data, SourceAnalysisArtifact.STRUCTURED_C_FACTS.value)
    analyzer = _validate_facts_header(value)
    availability = _domain_availability(value)
    units = value.get("units")
    if not isinstance(units, list) or not units:
        raise WorkflowError("structured_c_facts requires translation units")
    if value.get("unresolved_fact_domains") != []:
        raise WorkflowError("structured_c_facts cannot pass with unresolved fact domains")
    counts = [_validate_unit(unit, analyzer) for unit in units]
    _validate_domain_totals(availability, counts)


def _validate_facts_header(value: dict[str, object]) -> dict[str, object]:
    analyzer = value.get("analyzer")
    source_identity = value.get("source_checkout_identity")
    if value.get("schema_version") != 1:
        raise WorkflowError("structured_c_facts must be schema_version=1")
    if StructuredAnalysisStatus(value.get("status")) is not StructuredAnalysisStatus.READY:
        raise WorkflowError("structured_c_facts must be READY")
    if not isinstance(analyzer, dict) or len(str(analyzer.get("sha256", ""))) != 64:
        raise WorkflowError("structured_c_facts requires a hashed analyzer identity")
    if not isinstance(source_identity, dict) or source_identity.get("clean") is not True:
        raise WorkflowError("structured_c_facts requires a clean frozen source checkout")
    return analyzer


def _domain_availability(value: dict[str, object]) -> dict[str, FactAvailability]:
    domains = value.get("fact_domains")
    required_domains = {domain.value for domain in SemanticFactDomain}
    if not isinstance(domains, dict) or set(domains) != required_domains:
        raise WorkflowError("structured_c_facts does not cover every semantic fact domain")
    try:
        availability = {domain: FactAvailability(status) for domain, status in domains.items()}
    except (TypeError, ValueError) as error:
        raise WorkflowError("structured C fact domain has invalid availability") from error
    raw_only = {
        SemanticFactDomain.CFG,
        SemanticFactDomain.RECORD_LAYOUT,
        SemanticFactDomain.PREPROCESSOR,
        SemanticFactDomain.LLVM_IR,
    }
    if any(
        availability[domain.value] is not FactAvailability.STRUCTURED
        for domain in SemanticFactDomain
        if domain not in raw_only
    ):
        raise WorkflowError("a required structured C fact domain is empty")
    for domain in (SemanticFactDomain.PREPROCESSOR, SemanticFactDomain.LLVM_IR):
        if availability[domain.value] is not FactAvailability.RAW_VALIDATED:
            raise WorkflowError(f"structured C {domain.value} raw evidence is not validated")
    return availability


def _validate_domain_totals(
    availability: dict[str, FactAvailability], counts: list[tuple[int, int]]
) -> None:
    cfg_blocks = sum(item[0] for item in counts)
    layout_records = sum(item[1] for item in counts)
    expected = {
        SemanticFactDomain.CFG: (
            FactAvailability.STRUCTURED if cfg_blocks else FactAvailability.EMPTY_VALID
        ),
        SemanticFactDomain.RECORD_LAYOUT: (
            FactAvailability.STRUCTURED if layout_records else FactAvailability.EMPTY_VALID
        ),
    }
    if any(availability[domain.value] is not status for domain, status in expected.items()):
        raise WorkflowError("structured C domain availability does not match extracted facts")


VALIDATORS = MappingProxyType[SourceAnalysisArtifact, ArtifactValidator](
    {
        SourceAnalysisArtifact.STRUCTURED_C_FACTS: _structured_facts,
        SourceAnalysisArtifact.STRUCTURED_C_RAW_FACT: nonempty,
    }
)
