# Component integration: on-demand implementation guidance

For a new component, connect workspace membership and dependency, Components.toml, the consuming
crate dependency AND an actual Rust reference from a linked crate. A Cargo dependency alone need
not retain an otherwise unused component initializer. Match the target's init_component mechanism
and required initialization ordering; source examples below identify the current target version.

Before the first run, locally check actual linkage/initialization, ownership/lifetime and lock
scope. Encode the current behavior's initial state, valid transition and applicable rejection or
cleanup assertions together. Observe the initializer and required device behavior; compilation or
a booted shell alone is insufficient. Later concrete defects still require repair and verification.

These are navigation and implementation checks, not a new report or acceptance gate. Do not copy
another driver's protocol, resource model or initialization stage. No mandatory knowledge query
or full platform survey is required. Current evidence takes precedence over prior experience.
