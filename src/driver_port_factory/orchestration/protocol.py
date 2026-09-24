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
        "external_urls?:[url],gap?:{impact,repair_trigger}}]}. "
        "Lanes: source,target,qemu,hardware,test,tooling. Facet is a descriptive name. "
        "The controller adds source/driver_entry. Source/target/qemu use matching repositories; "
        "test/source_tests uses source; other test/tooling facets may use any repository. "
        "Hardware uses external documents, not repository_paths. Official originals use "
        "publisher_url and basis; mirrors use corroboration_urls. Unavailable evidence uses "
        "checked external_urls and gap. The controller owns hashes and provenance storage. "
        "Write this object to a file and invoke tool_runtime.submission_command with --kind "
        "proposal --decision submit. Do not put JSON in the final chat response. On repair "
        "preserve valid selections and add the needed originals."),
    "environment_recovery": TaskProtocol(completion=
        "Write environment-smoke.sh and the environment report, then invoke "
        "tool_runtime.submission_command with --kind report --decision pass. The controller "
        "runs the acceptance smoke; the final chat response is not a submission.", executable=True),
    "target_platform_study": TaskProtocol(completion=
        "Complete the target-platform study report, including the profile, target API evidence, "
        "one applicable analogous path and target-change/packaging evidence; write it to a file "
        "and invoke tool_runtime.submission_command with --kind report --decision pass. Use "
        "--decision rework --repair-stage or --decision blocked only under the shared protocol."),
    "analysis_review": TaskProtocol(completion=
        "Write the review report and invoke tool_runtime.submission_command with --kind report "
        "--decision pass. Use --decision rework --repair-stage or --decision blocked for the "
        "listed repair/blockage protocol."),
    "migration_contracts": TaskProtocol(completion=
        "Complete source analysis, contracts and test plan in one Markdown report, then invoke "
        "tool_runtime.submission_command with --kind report --decision pass."),
    "target_framework_enablement": TaskProtocol(completion=
        "Implement only the minimal target-platform framework/API changes required by the "
        "frozen target study and migration contracts in the target worktree. Run the narrowest "
        "target checks and affected regressions, write the report under .dpf-output, include "
        "necessity, alternatives, safety impact and rollback evidence, then invoke "
        "tool_runtime.submission_command with --kind report --decision pass. Do not edit the "
        "migrated driver or invent a fallback path."),
    "driver_implementation": TaskProtocol(completion=
        "Finish implementation and affected checks, then invoke tool_runtime.submission_command "
        "with --kind report --decision pass.", executable=True),
    "artifact_preparation": TaskProtocol(completion=
        "Write .dpf-output/runtime-artifact and executable .dpf-output/check-presence.sh. "
        "Put necessary variants under .dpf-output/harness/variants/ as regular files. "
        "The controller runs the checker and records artifact identities. "
        "Prepare artifact and checker covering payloads and reachable entrypoints for every retained "
        "runtime scenario using the production artifact and any necessary packaged variants. "
        "If packaging changed source/configuration, "
        "self-check affected changes and invoke tool_runtime.submission_command with --kind "
        "report --decision pass; the controller refreshes "
        "the implementation snapshot without another implementation turn.", executable=True),
    "public_qemu_validation": TaskProtocol(("PUBLIC_QEMU",),
        "Write .dpf-output/public-qemu.sh, using $DPF_RUNTIME_ARTIFACT for production. "
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
        "For final_evidence_review, use the frozen contract/test IDs only as acceptance oracles. "
        "Do not reopen evidence-and-design work or request evidence_closure, target_platform_study "
        "or migration_contracts. Route target framework capability/interface changes to "
        "target_framework_enablement, changed driver source to driver_implementation, authoritative "
        "runtime/CAS/checker/variant/entrypoint identity to artifact_preparation, and unchanged-artifact "
        "harness/oracle defects to public_qemu_validation. A stale or refreshed human-readable artifact "
        "receipt or worker summary is derived metadata, not a packaging defect, and must not trigger a "
        "new artifact or QEMU run. Write the reason in the report and invoke "
        "tool_runtime.submission_command with --decision rework --repair-stage <delivery prerequisite>. "
        "The reviewer never edits source or runtime files."
        if stage == "final_evidence_review" else
        "Within the current phase choose from instructions.repair_targets. Route missing original "
        "evidence/provenance/materials to evidence_closure; environment or executable-route defects "
        "to environment_recovery; target API/call-chain/init-order/target-change evidence to "
        "target_platform_study; C compiler/configuration/preprocessor/layout/ABI/effect facts, "
        "translation contracts, or test assertion provenance to migration_contracts; target framework "
        "capability/interface implementation to target_framework_enablement; changed driver source "
        "to driver_implementation; packaging/image identity to artifact_preparation; unchanged-artifact "
        "harness/oracle defects to public_qemu_validation. Do not route a semantic contract defect to "
        "evidence_closure merely because it is called an evidence gap. Write the reason in the report "
        "and invoke tool_runtime.submission_command with --decision rework --repair-stage <affected "
        "prerequisite>. Completed earlier phases are sealed. If a frozen earlier premise must change, "
        "report the concrete blocker and impact; never silently reopen it."
    )
    return {
        "report_action": "The report is evidence only. Append execution observations and self-checks to the file; use tool_runtime.submission_command to submit pass, operation, rework or blocked. The final chat response never changes workflow state.",
        "input_authority": "Current frozen_inputs supersede older versions in conversation or reports. "
            "repair_state OPEN is actionable; RESOLVED is history, not a new repair request.",
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
