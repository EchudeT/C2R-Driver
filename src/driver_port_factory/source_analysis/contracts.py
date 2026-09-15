from enum import StrEnum


class SourceAnalysisStage(StrEnum):
    SOURCE_CLOSURE = "source_closure"
    STRUCTURED_C_ANALYSIS = "structured_c_analysis"


class SourceAnalysisArtifact(StrEnum):
    SOURCE_CLOSURE = "source_closure"
    SOURCE_CLOSURE_REPORT = "source_closure_report"
    SOURCE_CLOSURE_VALIDATION_ATTEMPT = "source_closure_validation_attempt"
    COMPILE_MANIFEST = "compile_manifest"
    COMPILATION_DATABASE = "compilation_database"
    MATERIALS_MANIFEST = "source_closure_materials_manifest"
    KNOWLEDGE_REVISION = "knowledge_revision"
    STRUCTURED_C_FACTS = "structured_c_facts"
    STRUCTURED_C_ANALYSIS_REPORT = "structured_c_analysis_report"
    STRUCTURED_C_ANALYSIS_ATTEMPT = "structured_c_analysis_attempt"
    STRUCTURED_C_RAW_FACT = "structured_c_raw_fact"
    STRUCTURED_C_SEMANTIC_INDEX = "structured_c_semantic_index"
    STRUCTURED_C_COMMAND_RECORDS = "structured_c_command_records"


class SourceAnalysisEvent(StrEnum):
    CLOSURE_EXTENDED = "knowledge.source_closure_extended"


class SourceClosureStatus(StrEnum):
    CLOSED = "CLOSED"


class CoverageStatus(StrEnum):
    COVERED = "COVERED"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class ValidationStatus(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"


class ClosureCategory(StrEnum):
    SHARED_CORES = "shared_cores"
    HEADERS = "headers"
    MACROS_CONFIGURATION = "macros_configuration"
    CALLBACKS_FUNCTION_POINTERS = "callbacks_function_pointers"
    REGISTRATION_TABLES = "registration_tables"
    SOURCE_TESTS = "source_tests"
    FRAMEWORK_CONTRACTS = "framework_contracts"
