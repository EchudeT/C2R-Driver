# Driver Identity Intake

Identity confirmation prevents expensive acquisition for the wrong module.

## Required intake record

Record:

```text
source_platform
target_platform
user_supplied_driver_name
canonical_source_driver_name
source_driver_entry_or_repository_hint
device_family
bus_or_transport
candidate_device_ids
intended_subset
excluded_variants
identity_status: CONFIRMED | NEEDS_USER_CONFIRMATION
confirmation_basis
```

The user-supplied string alone is not proof of identity. Resolve it using existing workspace files first, then cheap authoritative metadata such as official source-tree indexes, configuration symbols, module aliases, device tables, or repository code search. At this stage, avoid repository clones, release archives, PDF downloads, toolchains, disk images, and broad crawls.

## Ambiguity conditions

Set `NEEDS_USER_CONFIRMATION` when any of these apply:

- multiple source drivers match the same colloquial device name;
- one module contains materially different bus front ends or device families;
- the same driver name exists in unrelated source trees;
- the requested name denotes a subsystem or class rather than one driver;
- QEMU models only one of several possible hardware variants and selecting it changes migration scope;
- spelling correction or alias expansion is not uniquely supported.

Ask one concise question containing candidate canonical names and their device/bus distinctions. Confirmation is required even when one candidate seems more likely. Do not frame a default as already selected.

## Confirmed scope

An identity is confirmed when one source driver entry and a concrete device/bus subset are known. Exact device IDs may remain a post-acquisition discovery if the canonical driver is otherwise unique, but freeze them before implementation. A later conflict between source device tables, hardware evidence, and QEMU support reopens intake rather than silently narrowing or changing the driver.

## Confirmation state machine (anti-loop)

Treat intake as a persisted state machine, not as a recurring conversational check:

    UNRESOLVED -> ONE_QUESTION_ISSUED -> WAITING_FOR_USER
    WAITING_FOR_USER + answer -> CONFIRMED_OR_ONE_NEW_CONFLICT
    CONFIRMED -> acquisition may begin

- Issue at most one clarification message for the current identity/version gate. Put all known candidates, the exact decision required, and the consequences of each choice in that one message.
- Persist the question, candidates, and the user's answer in the intake record. Once the user answers, do not ask the same question again merely because later evidence is incomplete; use the answer as authoritative and continue acquiring evidence.
- Version selection is normally an agent decision after identity confirmation. Pin the most recent mutually compatible source/target/QEMU revisions supported by concrete evidence and record the rule. Ask the user about versions only when alternatives materially change scope, ABI, license, required artifact, or experiment feasibility.
- If the user says “use the recommended/latest compatible version” or supplies a tag/commit, treat it as confirmation and proceed. Do not present another version menu unless a new concrete incompatibility is discovered.
- A new question is allowed only for a genuinely new conflict. State what new evidence changed and ask only that new question. Never alternate between “please choose” and internal reconsideration without new evidence.
- If state cannot persist in conversation, write intake.json or an equivalent local record before asking. On resume, read it first and continue from WAITING_FOR_USER or CONFIRMED; do not regenerate the original question.
