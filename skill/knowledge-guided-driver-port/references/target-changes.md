# Necessary Target-Platform Changes

Target-platform changes are permitted when they are necessary to integrate or correctly operate the migrated driver. Permission is not a license for opportunistic cleanup, redesign, or unrelated repair. Minimize both semantic scope and changed files.

## Change order

Choose the lowest sufficient level:

1. **Driver-owned change:** new driver/module files and driver-local tests, manifests, configuration, or harness code.
2. **Integration wiring:** the smallest existing registry, workspace manifest, build rule, device table, component selection, image/package list, or test entry needed to include the new driver.
3. **Target API/framework change:** a minimal target-platform implementation or interface change only when an evidenced driver contract cannot be implemented correctly through existing APIs or wiring.

Do not use level 3 merely to make the port easier or more idiomatic. First search the target source and knowledge base for existing extension points, analogous drivers, feature configuration, and documented alternatives.

## Necessity record

Before changing any pre-existing target file, record:

```text
change_id
driver_contract_ids
problem_and_observed_blocker
target_evidence
alternatives_considered_and_why_insufficient
selected_change_level
exact_files_and_symbols
smallest_expected_behavioral_effect
public_API_ABI_or_safety_impact
validation_plan
rollback_method
status
```

Freeze the unmodified target revision, dirty-state fingerprint, and baseline result first. Keep target modifications distinguishable from generated driver code—prefer a separate local commit or patch series when repository state permits—and preserve unrelated user changes.

## Safety constraints

- Follow the target's documented architecture, coding, safety, concurrency, lifecycle, error, build, and review rules.
- Preserve compatibility for existing drivers and configurations unless the confirmed migration contract strictly requires a change and its impact is explicitly validated.
- Do not weaken type, memory, privilege, isolation, locking, interrupt, validation, or error-handling guarantees to make the driver pass.
- Do not disable tests, warnings, linters, signatures, integrity checks, or security controls.
- Do not edit generated or vendored files when an authoritative source/configuration mechanism exists.
- Do not combine unrelated refactors, formatting sweeps, dependency upgrades, or target bug fixes with the migration.
- Keep new unsafe code minimal and document its local safety obligations and evidence.

If the necessary change would alter a public ABI, security model, allocator or scheduler semantics, broad interrupt/memory behavior, or multiple unrelated subsystems, stop and ask the user before implementation. Report the evidence, minimal proposed scope, risks, and alternatives. If evidence is insufficient, mark the dependent contract `BLOCKED_TARGET_CHANGE`.

## Validation and completion

After each target change, run the narrowest relevant target check, then the driver test, then existing affected regression checks. Compare against the frozen unmodified-target baseline. A target change is retained only if:

- the linked driver contract requires it;
- the chosen level is the lowest sufficient level;
- affected target checks do not regress;
- the QEMU evidence follows the expected path through the changed artifact;
- the necessity record and rollback patch remain current.

Remove diagnostic or superseded target changes before final reporting. List every retained pre-existing target file change separately from the migrated driver, with its rationale, risk, validation, and unresolved impact.
