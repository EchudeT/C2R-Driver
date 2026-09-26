# Externally anchored state-ledger event

```text
sequence:
batch_id:
task_id:
attempt_id:
evaluation_mode:
candidate_exposure_state: OPAQUE_ONLY | SEMANTICALLY_IMPORTED | NOT_APPLICABLE
previous_event_digest:
old_state:
new_state:
actor_role_identity_machine_workspace:
public_bundle_digest:
private_bundle_commitment:
candidate_bundle_digest:
claimed_event_time:
actor_signature:
external_WORM_transparency_or_timestamp_service:
external_log_sequence:
external_trusted_timestamp_receipt:
event_digest:
```

An actor signature authenticates the event but does not prove when it occurred. G0 accepts chronology only when every critical transition has an external receipt from a service the actor could not rewrite.
