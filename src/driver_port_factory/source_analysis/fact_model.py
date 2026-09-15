from enum import StrEnum


class StructuredAnalysisStatus(StrEnum):
    READY = "READY"
    FAIL = "FAIL"


class RawFactKind(StrEnum):
    TYPED_AST = "typed_ast"
    PREPROCESSED_SOURCE = "preprocessed_source"
    RECORD_LAYOUT = "record_layout"
    LLVM_IR = "llvm_ir"
    CFG = "cfg"


class FactAvailability(StrEnum):
    STRUCTURED = "STRUCTURED"
    RAW_VALIDATED = "RAW_VALIDATED"
    EMPTY_VALID = "EMPTY_VALID"


class SemanticFactDomain(StrEnum):
    TYPED_AST = "typed_ast"
    CODE_PROPERTY_GRAPH = "code_property_graph"
    CFG = "cfg"
    RECORD_LAYOUT = "record_layout"
    PREPROCESSOR = "preprocessor"
    LLVM_IR = "llvm_ir"
    CALLS = "calls"
    GLOBALS = "globals"
    EFFECTS = "effects"
    SOURCE_SPANS = "source_spans"


class CfgBlockRole(StrEnum):
    ENTRY = "ENTRY"
    EXIT = "EXIT"
