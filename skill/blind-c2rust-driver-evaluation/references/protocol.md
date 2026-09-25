# Roles, State, and Isolation Protocol

Blind evaluation is an information-flow property, not a directory naming convention. The official result is blind only when private test information cannot reach candidate production before the candidate is sealed, and candidate-specific observations cannot influence frozen tests or thresholds.

## Evaluation modes

Freeze one mode before semantic candidate inspection:

```text
PROSPECTIVE_BLIND
  private commitment < public export < migration start < candidate seal < execution
  eligible for: first-attempt held-out migration claims

POST_HOC_SEALED_BLIND
  opaque candidate acceptance < private-test design < private commitment
    < semantic candidate import < execution
  eligible for: immutable-candidate evaluation without private-test feedback
  not eligible for: prospective or first-attempt held-out migration claims

DEVELOPER_EVIDENCE
  candidate exposure or implementation preceded test freeze in the same actor/context
  eligible for: engineering evidence only
```

For `POST_HOC_SEALED_BLIND`, read `blind-ai-freeze.md`. A fresh blind-test AI must freeze executable
PMC/PEA and the private harness before seeing candidate contents; merely inventing tests after
candidate inspection is not blind.

## Experiment roles

Record one actor identity, machine or service identity, workspace, credentials, and responsibility for each role:

```text
CURATOR
  owns: split manifest, PMC, PEA, thresholds, generators, commitment
  must not: implement, choose, or repair the candidate

MIGRATION_OPERATOR
  owns: migrator version, task-local evidence, candidate, public tests
  must not: access PEA, private seeds, private results, evaluator credentials

EVALUATOR
  owns: sealed-candidate import, private execution, raw results
  must not: edit candidate or provide official-run feedback before unblinding

HARDWARE_OPERATOR
  owns: pinned devices, power/reset procedure, external workload and captures
  must not: rebuild or patch the candidate

AUDITOR
  owns: read-only inspection of commitments, logs, manifests and claims
```

Choose one active role per invocation or phase. A person may hold multiple non-conflicting duties only when the paper does not claim those duties are organizationally independent. `MIGRATION_OPERATOR` is incompatible with `CURATOR`, `EVALUATOR`, and any `HARDWARE_OPERATOR` that can see private workloads, captures or results for the same experiment. Curator and evaluator may be the same independent test team, but then the paper may claim producer/tester separation, not curator/evaluator separation. Record every combination explicitly and verify it in G0.

A blind-test AI may hold `CURATOR` and later `EVALUATOR` in separate recorded phases. It must enter
the curator phase with no candidate-semantic exposure, commit the private bundle, close that phase,
and only then import the candidate. Record `CURATOR_EVALUATOR_COMBINED`.

The default threat model is that candidate producers must never see private evaluation material before unblinding. An evaluator may inspect both the sealed candidate and PEA to judge it, but must not implement or repair the candidate. If a study requires the stronger property that no human can read both, split submission verification from a black-box evaluation service and record that stronger boundary.

## Minimum storage boundary

Use separately permissioned storage, not branches in one readable Git object database:

```text
benchmark-public/       readable by all roles
migration-workspace/    readable by migration operator; no private credentials
evaluation-private/     readable only by curator/evaluator
hardware-lab/           receives sealed artifact, not migration source edits
artifact-release/       receives disclosed materials after unblinding
```

The migration environment must not mount the evaluator repository, user home, shared shell history, evaluator caches, credentials, CI secrets, or host paths from which private data can be recovered. Internet access is permitted during dependency acquisition, build, and execution and is not an isolation gate or result condition. Record actual mounts, identities, environment variables, and dependency versions needed to interpret or reproduce the run.

The running candidate is also untrusted. Keep PEA, generators, checker code, salts, seeds, credentials and evaluator storage on an independent controller. Run the candidate in a separate VM, physical host, or an equivalently strong hardware-backed boundary. An ordinary same-UID process boundary or container sharing the controller's host kernel is insufficient for untrusted kernel or privileged driver code.

The candidate receives evaluation stimuli through the fixed public control/data protocol. It receives no private mounts, environment variables, command-line secrets, tokens or evaluator-storage route. General Internet, network, or IPC access does not by itself invalidate blindness or change a gate result. Capture serial output, traces, crashes and dumps outside the candidate and embargo them from the producer until the whole batch unblinds.

Separate LLM agents or subagents are not independent if they share conversation history, filesystem, tool logs, memory, or private credentials. Use a fresh process and context whose allowed inputs are exactly the public task bundle.

For a single-user local `POST_HOC_SEALED_BLIND` study, a fresh context plus an opaque pre-freeze
candidate digest can establish procedural blindness, but same-credential storage cannot establish
organizational independence. Label it `PROCESS_BLINDED`, record the weaker boundary, and keep all
private material outside the migration workspace. Candidate exposure in retained context still
forces `DEVELOPER_EVIDENCE`.

## Batch immutability

Freeze the migrator, prompts, generic knowledge rules, budgets, public adapters, and selection policy before the first held-out task. Start every task from a clean snapshot with no cross-task memory. Task-local knowledge derived from that task's allowed C source and documentation is permitted and must be logged, but it must not update the global migrator or later held-out tasks.

If the method intentionally learns between tasks, label the experiment `ONLINE_ADAPTATION`, publish the order and update history, and compare against a frozen no-learning baseline.

## Official state machines and ledger

Use a batch state plus one sub-state per task and attempt:

```text
BATCH_DRAFT
  -> CONTRACT_FROZEN
  -> PRIVATE_BUNDLE_COMMITTED
  -> BATCH_RUNNING
  -> ALL_TASKS_TERMINAL
  -> UNBLINDED
  -> REPORTED

TASK_ATTEMPT_DRAFT
  -> PUBLIC_TASK_EXPORTED
  -> MIGRATION_RUNNING
  -> CANDIDATE_SEALED | MIGRATION_TERMINAL_FAILURE
  -> PRIVATE_EVALUATION_RUNNING
  -> PRIVATE_EVALUATION_TERMINAL
  -> HARDWARE_TERMINAL | HARDWARE_NOT_PLANNED
```

For `POST_HOC_SEALED_BLIND`, use the separate batch sequence:

```text
POST_HOC_DRAFT
  -> POST_HOC_CANDIDATE_ACCEPTED
  -> CONTRACT_FROZEN
  -> PRIVATE_BUNDLE_COMMITTED
  -> POST_HOC_RUNNING
  -> ALL_TASKS_TERMINAL
  -> UNBLINDED
  -> REPORTED
```

Never return from `CANDIDATE_SEALED` to candidate editing within the same official attempt. A correction becomes a new candidate and attempt. Preserve the first attempt. Do not unblind until every task and attempt in the frozen batch reaches its declared terminal condition.

Write every critical transition to a hash-chained ledger and immediately anchor it in an external append-only transparency log, WORM store, or trusted timestamp service that the actor cannot rewrite:

```text
sequence
batch_task_attempt_ids
previous_event_digest
old_state_and_new_state
actor_role_machine_workspace
public_private_candidate_digests_as_applicable
claimed_event_time_and_actor_signature
external_log_sequence_and_trusted_timestamp_receipt
```

An actor signature authenticates the submitted event but does not establish when it occurred. Never
accept an unanchored self-signed chain as chronology evidence. `G0_ISOLATION` must verify the selected
mode's order. For `PROSPECTIVE_BLIND`, verify `contract/private commitment < public export <
migration start < candidate seal < private execution < batch unblind`. For
`POST_HOC_SEALED_BLIND`, verify `opaque candidate acceptance < private-test design < private
commitment < semantic import < private execution < unblind`. A pre-existing candidate cannot be
retroactively counted as held-out first-attempt evidence, even when its post-hoc sealed evaluation
is otherwise valid.

## Commitment

At the mode-specific freeze point, create fresh random salt and compute a commitment over a
canonical archive or manifest of the private bundle. In `PROSPECTIVE_BLIND` this is before public
export; in `POST_HOC_SEALED_BLIND` it is after opaque candidate acceptance but before semantic
candidate import:

```text
commitment = SHA256(canonical_private_bundle || salt)
```

Store the commitment, bundle digest, counts, thresholds, split digest, and owner signature in an external WORM/transparency log or trusted timestamp service and retain its receipt. Reveal the bundle and salt only after the entire frozen batch reaches `ALL_TASKS_TERMINAL` and enters `UNBLINDED`; otherwise give artifact reviewers controlled access and a signed verification result after the same point.

A commitment proves that the bundle did not change after commitment. It does not prove that the migrator never read it; access isolation and logs establish that separate property.

## Allowed corrections

A private assertion may be declared invalid only for a predeclared reason such as harness failure, contradiction with the frozen PMC, nondeterministic infrastructure outside its tolerance, or failure on a validated C reference when equivalence was required. Preserve the original result, correction record, reviewer, reason, and rerun of all affected candidates. Do not call an assertion invalid merely because the candidate failed it.
