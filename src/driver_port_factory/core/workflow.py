from __future__ import annotations

from .models import ActorRole, EvaluationMode, ProjectConfig, StageOwner, StageSpec


def _stage(
    name: str,
    description: str,
    owner: StageOwner,
    dependency: str | None,
    outputs: tuple[str, ...] = (),
    roles: tuple[ActorRole, ...] = (),
    *,
    accept_failed: bool = False,
) -> StageSpec:
    return StageSpec(
        name=name,
        description=description,
        owner=owner,
        dependencies=(dependency,) if dependency else (),
        required_outputs=outputs,
        allowed_roles=roles,
        accept_failed_dependencies=accept_failed,
    )


def migration_workflow(config: ProjectConfig) -> list[StageSpec]:
    roles = (ActorRole.DEVELOPER, ActorRole.MIGRATION_OPERATOR)
    specs = [
        _stage("project_init", "Freeze project identity and control configuration.", StageOwner.STATIC, None, ("project_manifest",), roles),
    ]
    previous = "project_init"
    if config.evaluation_mode is EvaluationMode.PROSPECTIVE_BLIND:
        specs.append(
            _stage(
                "blind_binding",
                "Import the prospective public bundle and curator commitment.",
                StageOwner.STATIC,
                previous,
                ("public_bundle", "curator_commitment"),
                roles,
            )
        )
        previous = "blind_binding"

    rows = [
        ("driver_identity", "Resolve one concrete driver, device, and bus scope.", StageOwner.HYBRID, ("identity_record",)),
        ("revision_selection", "Pin source, target, and QEMU revisions.", StageOwner.HYBRID, ("revision_manifest",)),
        ("evidence_acquisition", "Acquire the minimum provenance-tracked evidence closure.", StageOwner.HYBRID, ("acquisition_manifest",)),
        ("environment_recovery", "Establish artifact mode and a concrete executable experiment route.", StageOwner.HYBRID, ("experiment_route",)),
        ("knowledge_base", "Build or validate the evidence knowledge base and query contract.", StageOwner.STATIC, ("kb_status", "kb_query_contract")),
        ("target_platform_study", "Build the target profile, API evidence table, and analogous call chain.", StageOwner.CODEX, ("target_profile", "target_api_evidence")),
        ("source_closure", "Close the behaviorally required C dependency set.", StageOwner.HYBRID, ("source_closure",)),
        ("structured_c_analysis", "Extract typed AST, CFG, layout, call, global, and effect facts.", StageOwner.STATIC, ("structured_c_facts",)),
        ("migration_contracts", "Convert evidence into hardware, platform, safety, and lifecycle obligations.", StageOwner.HYBRID, ("migration_contracts",)),
        ("test_adaptation", "Triage source tests and preserve portable device scenarios and oracles.", StageOwner.HYBRID, ("test_port_matrix",)),
        ("rust_design", "Design ownership, concurrency, error, and unsafe boundaries.", StageOwner.CODEX, ("rust_design",)),
        ("rust_implementation", "Implement the evidenced contracts incrementally in Rust.", StageOwner.CODEX, ("driver_source",)),
        ("target_compliance", "Review target API, style, safety, lifecycle, and integration rules.", StageOwner.HYBRID, ("compliance_report",)),
        ("artifact_preparation", "Build or inject the runtime artifact and prove its identity.", StageOwner.STATIC, ("runtime_artifact", "artifact_identity")),
        ("public_qemu_validation", "Run the public QEMU evidence ladder.", StageOwner.STATIC, ("public_qemu_report",)),
    ]
    for name, description, owner, outputs in rows:
        specs.append(_stage(name, description, owner, previous, outputs, roles))
        previous = name

    specs.append(
        _stage(
            "public_repair",
            "Diagnose public failures and run bounded narrow repair attempts.",
            StageOwner.HYBRID,
            previous,
            ("public_repair_report",),
            roles,
            accept_failed=True,
        )
    )
    previous = "public_repair"
    tail = [
        ("completion_audit", "Audit contract, implementation, test, and runtime coverage.", StageOwner.STATIC, ("evidence_audit",)),
        ("candidate_sealing", "Seal an immutable candidate manifest and digest.", StageOwner.STATIC, ("candidate_manifest",)),
    ]
    for name, description, owner, outputs in tail:
        specs.append(_stage(name, description, owner, previous, outputs, roles))
        previous = name

    if config.evaluation_mode is EvaluationMode.POST_HOC_SEALED_BLIND:
        specs.append(
            _stage(
                "opaque_digest_export",
                "Export and externally anchor only the opaque candidate digest.",
                StageOwner.STATIC,
                previous,
                ("candidate_digest_anchor",),
                roles,
            )
        )
    elif config.evaluation_mode is EvaluationMode.PROSPECTIVE_BLIND:
        specs.append(
            _stage(
                "candidate_transfer",
                "Transfer the sealed candidate to the independent evaluator.",
                StageOwner.STATIC,
                previous,
                ("candidate_transfer_record",),
                roles,
            )
        )
    return specs


def curator_workflow(config: ProjectConfig) -> list[StageSpec]:
    role = (ActorRole.CURATOR,)
    specs = [_stage("project_init", "Freeze curator project configuration.", StageOwner.STATIC, None, ("project_manifest",), role)]
    previous = "project_init"
    if config.evaluation_mode is EvaluationMode.POST_HOC_SEALED_BLIND:
        specs.append(
            _stage(
                "opaque_candidate_acceptance",
                "Accept and anchor an opaque candidate digest before candidate inspection.",
                StageOwner.STATIC,
                previous,
                ("candidate_digest_anchor",),
                role,
            )
        )
        previous = "opaque_candidate_acceptance"
    elif config.evaluation_mode is not EvaluationMode.PROSPECTIVE_BLIND:
        raise ValueError("curator role requires a blind evaluation mode")

    for name, description, owner, outputs in [
        ("driver_split", "Freeze driver family and task splits.", StageOwner.HYBRID, ("split_manifest",)),
        ("contract_freeze", "Freeze evidence-backed PMC and private PEA.", StageOwner.CODEX, ("public_contract", "private_assertions")),
        ("reference_calibration", "Validate assertions against the C reference and mutations.", StageOwner.HYBRID, ("reference_calibration",)),
        ("private_bundle_sealing", "Freeze thresholds, generators, harness, and commitment.", StageOwner.STATIC, ("private_bundle_commitment",)),
        ("public_bundle_export", "Export only the public task bundle.", StageOwner.STATIC, ("public_bundle",)),
    ]:
        specs.append(_stage(name, description, owner, previous, outputs, role))
        previous = name
    return specs


def evaluator_workflow(config: ProjectConfig) -> list[StageSpec]:
    role = (ActorRole.EVALUATOR,)
    specs = [_stage("project_init", "Freeze evaluator project configuration.", StageOwner.STATIC, None, ("project_manifest",), role)]
    previous = "project_init"
    rows = [
        ("evaluation_inputs", "Accept candidate, commitment, chronology, and environment identity.", ("evaluation_input_manifest",)),
        ("isolation_gate", "Verify role, material, credential, and feedback boundaries.", ("isolation_report",)),
        ("reproducible_build", "Build the exact sealed candidate from frozen inputs.", ("reproducible_build_report",)),
        ("mandatory_contracts", "Run frozen mandatory contract gates.", ("contract_report",)),
        ("external_functionality", "Run external black-box functionality tests.", ("functionality_report",)),
        ("differential_execution", "Compare C reference and Rust candidate behavior.", ("differential_report",)),
        ("fault_injection", "Run frozen fault schedules.", ("fault_report",)),
        ("mutation_adequacy", "Measure private assertion mutation sensitivity.", ("mutation_report",)),
        ("stress", "Run frozen stress and concurrency tests.", ("stress_report",)),
        ("performance", "Run frozen performance thresholds.", ("performance_report",)),
        ("hardware_subset", "Run the preselected real-hardware subset.", ("hardware_report",)),
        ("evaluation_report", "Seal complete results without candidate repair.", ("evaluation_report",)),
    ]
    for name, description, outputs in rows:
        specs.append(_stage(name, description, StageOwner.INDEPENDENT, previous, outputs, role))
        previous = name
    return specs


def auditor_workflow(config: ProjectConfig) -> list[StageSpec]:
    role = (ActorRole.AUDITOR,)
    specs = [
        _stage(
            "project_init",
            "Freeze read-only auditor project configuration.",
            StageOwner.STATIC,
            None,
            ("project_manifest",),
            role,
        )
    ]
    previous = "project_init"
    for name, description, outputs in [
        ("audit_inputs", "Import manifests, commitments, ledgers, and claims read-only.", ("audit_input_manifest",)),
        ("independence_audit", "Verify actor, context, workspace, material, and feedback separation.", ("independence_report",)),
        ("commitment_audit", "Verify chronology, commitments, bundle digests, and candidate identity.", ("commitment_report",)),
        ("claim_audit", "Check reported claims against frozen modes and observed evidence.", ("claim_report",)),
        ("audit_report", "Seal verified and missing independence evidence without changing bundles.", ("audit_report",)),
    ]:
        specs.append(
            _stage(name, description, StageOwner.INDEPENDENT, previous, outputs, role)
        )
        previous = name
    return specs


def workflow_for(config: ProjectConfig) -> list[StageSpec]:
    if config.actor_role in {ActorRole.DEVELOPER, ActorRole.MIGRATION_OPERATOR}:
        return migration_workflow(config)
    if config.actor_role is ActorRole.CURATOR:
        return curator_workflow(config)
    if config.actor_role is ActorRole.EVALUATOR:
        return evaluator_workflow(config)
    if config.actor_role is ActorRole.AUDITOR:
        return auditor_workflow(config)
    raise ValueError(f"unsupported actor role: {config.actor_role.value}")
