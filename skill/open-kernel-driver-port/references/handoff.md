# Bootstrap-to-Migration Handoff

The entry workflow hands a frozen, local evidence environment to `knowledge-guided-driver-port`; it does not merely tell the downstream phase that downloads exist.

## Required handoff record

```text
source_platform
target_platform
confirmed_driver_name
device_family_bus_and_ids
included_and_excluded_variants
source_revision_and_paths
target_revision_and_path
target_platform_profile_path_and_status
target_api_evidence_table_and_analogous_trace
target_knowledge_probe_results_and_gaps
qemu_revision_and_device_model
artifact_mode_and_runtime_inputs
qemu_invocation_or_runner
experiment_ready_run_and_evidence
driver_insertion_or_packaging_path
source_test_paths
workspace_root
upstream_read_only_paths
initial_driver_owned_paths
proposed_preexisting_target_changes_and_necessity_records
materials_manifest
knowledge_base_skill_name_and_path
knowledge_status_command_and_result
knowledge_build_search_show_commands
known_evidence_gaps
environment_recovery_attempts_and_remaining_external_blockers
target_integration_path_and_required_change_level
local_git_state
evaluation_mode: DEVELOPER | BLIND_CANDIDATE
blind_experiment_task_and_attempt_ids
public_task_bundle_digest
public_private_bundle_commitment_and_timestamp_receipt
append_only_state_ledger_path_and_previous_digest
public_migration_contract_path_and_digest
public_control_plane_version
frozen_migrator_identity_and_budget
candidate_bundle_format
private_evaluator_material_access: MUST_BE_FALSE
held_out_batch_no_cross_task_update_record
```

## Downstream loading

When optional platform navigation was used, carry its selected topic paths and the existing analysis/evidence record location in this handoff. Preserve the actual frozen revisions/configuration and unresolved questions. Do not copy navigation pages or create a separate selection artifact.

Read the sibling `../knowledge-guided-driver-port/SKILL.md` completely and follow its reference routing. Supply the generated/discovered knowledge-base Skill as the mandatory evidence interface. The downstream Skill must not reacquire or silently change frozen upstream revisions; if it discovers an evidence gap, return to the entry Skill's acquisition and knowledge rebuild gates, append provenance, and issue a revised handoff.

The downstream source-closure step may prove that more files are required. Acquire only those named dependencies, update the manifest/index, and preserve the previous state in local history. A new device family, bus front end, or materially different driver entry requires user reconfirmation before acquisition.

The handoff must not say merely “build information missing.” It must name the selected artifact mode, the QEMU route already exercised or ready to exercise, and how the migrated driver will enter that artifact. If only model-level experimentation is currently runnable, say so and keep target integration recovery active.

## Target change boundary

List driver-owned paths and any expected pre-existing target-file changes before implementation. Prefer module-local extension points. When they are insufficient, the downstream Skill may make only the lowest-level necessary target change under `target-changes.md`, with evidence, alternatives, exact scope, compatibility/safety analysis, validation, and rollback. Unknown or broad changes are not implicitly authorized; use `BLOCKED_TARGET_CHANGE` or ask the user when the policy requires it.

Remote operations after handoff are limited to small, read-only evidence gap fills routed back through acquisition. No remote builds, pushes, publications, PRs, or service-side changes are part of the workflow.

## Blind-candidate handoff

When `evaluation_mode` is `BLIND_CANDIDATE`, this handoff contains no private assertion, evaluator checker, private seed, fault schedule, mutation survival result, expected hidden output, or evaluator credential. Public requirements and public test/control protocols are allowed. The downstream actor is the migration operator, not the independent curator or evaluator.

The same frozen migration workflow must be used for the full held-out batch. Task-local evidence and knowledge indexes may be built from each public task, but they must not update global prompts, Skills, repair rules, public adapters, or subsequent held-out tasks. Preserve the batch identity and clean-start evidence in the handoff.
