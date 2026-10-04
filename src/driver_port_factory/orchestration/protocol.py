"""Worker/controller contract: capabilities and report actions have one owner."""
from dataclasses import dataclass



@dataclass(frozen=True)
class TaskProtocol:
    operations: tuple[str, ...] = ()
    completion: str = "Complete the requested Markdown report."
    executable: bool = False


TASKS = {
    "repository_acquisition": TaskProtocol(completion=
        "Write one JSON object containing repositories:[{role,url,ref}] to a file in the "
        "current workspace, then invoke tool_runtime.submission_command with --kind proposal "
        "--decision submit. Include one source, target and qemu entry using upstream URLs. "
        "The controller fetches/reuses caches and resolves revisions; do not duplicate "
        "repository downloads or provenance records. Do not put JSON in the final chat response."),
    "evidence_closure": TaskProtocol(completion=
        "Write {facets:[{lane,facet,rationale,repository_paths?:[{repository,path}],"
        "external_documents?:[{url,publisher_url,basis}|{url,corroboration_urls:[url]}],"
        "external_urls?:[url],gap?:{impact,repair_trigger,basis?:[{lane,facet}]}}]}. "
        "Lanes: source,target,qemu,hardware,test,tooling. Facet is a nonempty token without "
        "whitespace (for example register_state_machine); put descriptive prose in rationale. "
        "repository_paths may name tracked files or narrowly scoped directories in the frozen "
        "revision. The controller expands directories into exact tracked file locators and "
        "deduplicates overlap within each facet; do not manually enumerate a selected subtree. "
        "Select individual files when only part of a broad directory is relevant. "
        "The controller adds source/driver_entry. Source/target/qemu use matching repositories; "
        "test/source_tests uses source; other test/tooling facets may use any repository. "
        "Hardware uses external documents, not repository_paths. Official originals use "
        "publisher_url and basis; mirrors use corroboration_urls containing byte-identical "
        "copies of the same document from independent publishers after redirects, not different "
        "manual versions or two URLs on one publisher. Unavailable evidence uses "
        "external_urls for controller retrieval and gap, or gap.basis referencing other controlled "
        "facets whose originals support the limitation. With basis, omit unnecessary URL locators; "
        "a gap stays a gap and repository evidence does not become hardware authority. "
        "Follow reference_material.evidence_reuse: shared snapshot, frozen local originals, then "
        "external material for explicit gaps only. Do not curl web copies/status URLs just to "
        "reconfirm local Git provenance. The controller owns downloads, hashes and provenance storage. "
        "Write this object to a file and invoke tool_runtime.submission_command with --kind "
        "proposal --decision submit. Do not put JSON in the final chat response. On repair "
        "preserve valid selections and add the needed originals."),
    "environment_recovery": TaskProtocol(completion=
        "Use managed bootstrap to verify the selected baseline build and guest boot. Submit its "
        "returned report with tool_runtime.submission_command --kind report --decision pass. "
        "The controller binds that receipt without a second device probe. Unsupported adapters "
        "use a minimal route check through prepare_smoke; keep actual limitations explicit. "
        "Use the fixed image, firmware and accelerator; no fallback.", executable=True),
    "target_platform_study": TaskProtocol(completion=
        "Write one joint analysis report.md and report.route.json: source obligations, coarse "
        "target route, design-changing premises and only necessary minimal probes, contracts "
        "with distinguishing assertions, and meaningful implementation behaviors. "
        "Use direct source citations or optional knowledge retrieval for concrete gaps. "
        "No platform survey, second plan, probe quota or separate knowledge report. "
        "Submit the report with tool_runtime.submission_command --kind report --decision pass; "
        "the controller freezes the index and seeds implementation. Report an unresolved "
        "critical prerequisite as blocked rather than claiming verification."),
    "analysis_review": TaskProtocol(completion=
        "Write the review report and invoke tool_runtime.submission_command with --kind report "
        "--decision pass. Use --decision rework --repair-stage or --decision blocked for the "
        "listed repair/blockage protocol."),
    "target_framework_enablement": TaskProtocol(completion=
        "Complete one coherent delivery from the frozen analysis: minimal target API changes, "
        "Rust driver and shared integration, public tests and runnable artifact preparation. "
        "Create .dpf-output/runtime-artifact, check-presence.sh, implementation-smoke.sh, "
        "public-qemu.sh and required harness variants. Follow the implementation smoke and "
        "execution rules; use managed execution for a ready public suite, inspect its results "
        "and acknowledge the same report in-task when complete. Downstream capture reuses "
        "matching receipts. Record target-change necessity, alternatives, safety effects and "
        "actual check results in one report, distinguishing pending public tests. Submit with "
        "tool_runtime.submission_command --kind report --decision pass. The controller captures "
        "separate evidence checkpoints and performs the ordinary implementation checks; "
        "file presence alone does not establish correctness.", executable=True),
    "driver_implementation": TaskProtocol(completion=
        "Implement the driver, required target framework adaptations and shared integration "
        "as one coherent task, retaining target-change necessity and safety evidence in the "
        "same report. Finish affected checks. Build the current driver into "
        ".dpf-output/runtime-artifact and write .dpf-output/check-presence.sh plus "
        ".dpf-output/implementation-smoke.sh. The controller runs presence and a bounded "
        "functional QEMU smoke before accepting implementation. Use $DPF_RUNTIME_ARTIFACT, "
        "put helpers in .dpf-output/harness and fresh logs under .dpf-output/qemu-runs. "
        "Keep the smoke under 300 seconds; return zero only when all smoke assertions pass. "
        "Check component initialization, exact device identity, registration, successful "
        "probe/binding/readiness and one applicable data operation (network: one TX and RX "
        "with externally checked payloads). Derive device identity and execution environment "
        "from this project's frozen source, target and environment evidence. "
        "Save command, image identity, logs, actual exit/timeout and oracle results; an "
        "expected guest timeout is acceptable only after the assertions pass. The controller "
        "runs a deterministic runtime-premise preflight before QEMU and records every finding "
        "with an exact path and line. Inspect and repair failures in this stage before resubmitting; "
        "this does not replace the full public ladder. If a finding proves an absent target "
        "capability or framework interface, repair it coherently with driver integration; do not "
        "report a repairable implementation or packaging defect as BLOCKED. Describe assertions "
        "and results in the report, then invoke "
        "tool_runtime.submission_command with --kind report --decision pass.", executable=True),
    "artifact_preparation": TaskProtocol(completion=
        "Write .dpf-output/runtime-artifact and executable .dpf-output/check-presence.sh. "
        "Put necessary variants under .dpf-output/harness/variants/ as regular files. "
        "The controller runs the checker, repeats the generic runtime-premise preflight and records "
        "artifact identities. Marker text is not a runtime artifact. "
        "Prepare artifact and checker covering payloads and reachable entrypoints for every retained "
        "runtime scenario using the production artifact and any necessary packaged variants. "
        "If packaging changed source/configuration, "
        "self-check affected changes and invoke tool_runtime.submission_command with --kind "
        "report --decision pass; the controller refreshes "
        "the implementation snapshot without another implementation turn.", executable=True),
    "public_qemu_validation": TaskProtocol(("PUBLIC_QEMU",),
        "Write .dpf-output/public-qemu.sh, using $DPF_RUNTIME_ARTIFACT for production. "
        "Use the selected target route and required container boundary, mounting the worktree "
        "and runtime and recording image identity when applicable. "
        "Return zero only when its required oracles pass. Invoke tool_runtime.submission_command "
        "with --kind report --decision operation --operation PUBLIC_QEMU, then inspect "
        "controller_execution and update the same report. After the final audit invoke the "
        "same command with --decision pass.", True),
    "final_evidence_review": TaskProtocol(completion=
        "Write the review report and invoke tool_runtime.submission_command with --kind report "
        "--decision pass. Use --decision rework --repair-stage or --decision blocked for the "
        "listed repair/blockage protocol."),
}

REPAIR_TARGETS = frozenset({"evidence_closure", "environment_recovery", "target_platform_study",
    "migration_contracts", "target_framework_enablement", "driver_implementation",
    "artifact_preparation", "public_qemu_validation"})


def describe(stage):
    task = TASKS.get(stage)
    if task is None:
        return None
    repair = (
        "Use current contract/test IDs. A concrete counterexample to an analysis premise may request "
        "the smallest permitted analysis prerequisite in instructions.repair_targets. Cite the "
        "contradiction, affected obligations and necessary revision; do not reopen resolved doubts. "
        "Route all target framework, shared integration and driver source "
        "changes to driver_implementation, authoritative "
        "runtime/CAS/checker/variant/entrypoint identity to artifact_preparation, and unchanged-artifact "
        "harness/oracle defects to public_qemu_validation. A stale or refreshed human-readable artifact "
        "receipt or worker summary is derived metadata, not a packaging defect, and must not trigger a "
        "new artifact or QEMU run. Write the reason in the report and invoke "
        "tool_runtime.submission_command with --decision rework --repair-stage <delivery prerequisite>. "
        "The reviewer never edits source or runtime files."
        if stage == "final_evidence_review" else
        "Choose only from instructions.repair_targets. Route missing original "
        "evidence/provenance/materials to evidence_closure; broken baseline build/boot routes "
        "to environment_recovery; missing future device/backend launch design to migration_contracts "
        "without requiring its execution before implementation; target API/call-chain/init-order/target-change evidence to "
        "target_platform_study; C compiler/configuration/preprocessor/layout/ABI/effect facts, "
        "translation contracts, or test assertion provenance to migration_contracts; target framework "
        "capability/interface implementation and changed driver source "
        "to driver_implementation; packaging/image identity to artifact_preparation; unchanged-artifact "
        "harness/oracle defects to public_qemu_validation. Do not route a semantic contract defect to "
        "evidence_closure merely because it is called an evidence gap. Write the reason in the report "
        "and invoke tool_runtime.submission_command with --decision rework --repair-stage <affected "
        "prerequisite>. Revise an earlier premise only with concrete contradictory evidence and "
        "affected contract IDs; preserve unrelated conclusions and all historical observations."
    )
    return {
        "report_action": "The report is evidence only. Append execution observations and self-checks to the file; use tool_runtime.submission_command to submit pass, operation, rework or blocked. The final chat response never changes workflow state.",
        "input_authority": "Current frozen_inputs supersede older versions in conversation or reports. "
            "Previously supplied inputs are not necessarily read or verified.",
        "operations": list(task.operations),
        "operation_delivery": ("Use tool_runtime.submission_command with --decision operation "
                               "and --operation for a controller operation. An operation request "
                               "is not completion, blockage or prerequisite repair. Resume this "
                               "same task after its receipt; missing receipts require the local "
                               "operation, not upstream repair."
                               if task.operations else
                               "No worker-requested controller operations in this task; submit "
                               "the deliverable through tool_runtime.submission_command."),
        "completion": task.completion,
        "repair": repair,
        "blocked": "Continue repairing build, dependency, configuration and script failures within your current authority in this stage; an unsuccessful attempt or an identified next repair is not a blocker. Use tool_runtime.submission_command with --decision blocked only when further progress requires unavailable external access/resources, user authority/decision, a change to a sealed premise, or exhausted causal repairs with no meaningful progress. Explain the evidence, alternatives attempted and exact condition needed to resume in the report.",
    }
