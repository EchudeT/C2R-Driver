from enum import StrEnum


class RelationKind(StrEnum):
    AST_CHILD = "AST_CHILD"
    REFERENCES_DECL = "REFERENCES_DECL"
    DIRECT_CALL_TARGET = "DIRECT_CALL_TARGET"
    FUNCTION_POINTER_TARGET = "FUNCTION_POINTER_TARGET"
    INDIRECT_CALL_TARGET = "INDIRECT_CALL_TARGET"


class CallDispatch(StrEnum):
    DIRECT = "DIRECT"
    INDIRECT_RESOLVED = "INDIRECT_RESOLVED"
    INDIRECT_UNRESOLVED = "INDIRECT_UNRESOLVED"


class CallResolutionBasis(StrEnum):
    CALLEE_EXPRESSION = "CALLEE_EXPRESSION"
    DECLARATION_POINTS_TO = "DECLARATION_POINTS_TO"
    CLOSURE_POINTS_TO = "CLOSURE_POINTS_TO"


class PointerTargetStatus(StrEnum):
    EXACT = "EXACT"
    UNKNOWN = "UNKNOWN"


class ClangStorageClass(StrEnum):
    STATIC = "static"


class EffectKind(StrEnum):
    CALL = "CALL"
    ASSIGNMENT = "ASSIGNMENT"
    INCREMENT_DECREMENT = "INCREMENT_DECREMENT"
    RETURN = "RETURN"
    GOTO = "GOTO"
    LOOP_CONTROL = "LOOP_CONTROL"
    ATOMIC = "ATOMIC"
    INLINE_ASM = "INLINE_ASM"
    VOLATILE_TYPED_EXPRESSION = "VOLATILE_TYPED_EXPRESSION"


class ClangNodeKind(StrEnum):
    TRANSLATION_UNIT_DECL = "TranslationUnitDecl"
    FUNCTION_DECL = "FunctionDecl"
    VAR_DECL = "VarDecl"
    PARM_VAR_DECL = "ParmVarDecl"
    FIELD_DECL = "FieldDecl"
    TYPEDEF_DECL = "TypedefDecl"
    RECORD_DECL = "RecordDecl"
    CXX_RECORD_DECL = "CXXRecordDecl"
    COMPOUND_STMT = "CompoundStmt"
    CALL_EXPR = "CallExpr"
    DECL_REF_EXPR = "DeclRefExpr"
    MEMBER_EXPR = "MemberExpr"
    INIT_LIST_EXPR = "InitListExpr"
    IMPLICIT_CAST_EXPR = "ImplicitCastExpr"
    BINARY_OPERATOR = "BinaryOperator"
    UNARY_OPERATOR = "UnaryOperator"
    IF_STMT = "IfStmt"
    SWITCH_STMT = "SwitchStmt"
    CASE_STMT = "CaseStmt"
    DEFAULT_STMT = "DefaultStmt"
    WHILE_STMT = "WhileStmt"
    DO_STMT = "DoStmt"
    FOR_STMT = "ForStmt"
    GOTO_STMT = "GotoStmt"
    INDIRECT_GOTO_STMT = "IndirectGotoStmt"
    LABEL_STMT = "LabelStmt"
    BREAK_STMT = "BreakStmt"
    CONTINUE_STMT = "ContinueStmt"
    RETURN_STMT = "ReturnStmt"
    CONDITIONAL_OPERATOR = "ConditionalOperator"
    BINARY_CONDITIONAL_OPERATOR = "BinaryConditionalOperator"
    ATOMIC_EXPR = "AtomicExpr"
    GCC_ASM_STMT = "GCCAsmStmt"
    MS_ASM_STMT = "MSAsmStmt"


class COperator(StrEnum):
    ASSIGN = "="
    MULTIPLY_ASSIGN = "*="
    DIVIDE_ASSIGN = "/="
    REMAINDER_ASSIGN = "%="
    ADD_ASSIGN = "+="
    SUBTRACT_ASSIGN = "-="
    SHIFT_LEFT_ASSIGN = "<<="
    SHIFT_RIGHT_ASSIGN = ">>="
    BIT_AND_ASSIGN = "&="
    BIT_XOR_ASSIGN = "^="
    BIT_OR_ASSIGN = "|="
    INCREMENT = "++"
    DECREMENT = "--"
    ADDRESS_OF = "&"


ASSIGNMENT_OPERATORS = frozenset(
    {
        COperator.ASSIGN,
        COperator.MULTIPLY_ASSIGN,
        COperator.DIVIDE_ASSIGN,
        COperator.REMAINDER_ASSIGN,
        COperator.ADD_ASSIGN,
        COperator.SUBTRACT_ASSIGN,
        COperator.SHIFT_LEFT_ASSIGN,
        COperator.SHIFT_RIGHT_ASSIGN,
        COperator.BIT_AND_ASSIGN,
        COperator.BIT_XOR_ASSIGN,
        COperator.BIT_OR_ASSIGN,
    }
)
INCREMENT_DECREMENT_OPERATORS = frozenset({COperator.INCREMENT, COperator.DECREMENT})
