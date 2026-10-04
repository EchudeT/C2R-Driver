# Native environment verification, 2026-10-04

Scope: shared experimental infrastructure, not translated-driver quality or model cost.

Real Docker/KVM/OVMF observations:

- VirtIO RNG, Block, Network and NVMe PCI original controls built, booted and passed their complete configured upstream test sets.
- Each independently hollowed target built and booted. Its corresponding device tests failed with device-absence evidence; skips were not accepted as passes.
- The production DPF runtime adapter reproduced all four positive and all four negative controls using an externally supplied final ISO path.
- The standalone environment distribution, invoked with Python site-packages disabled, ran all 15 original RNG test functions successfully.
- Each target seed has one root commit, no remotes, alternates or unreachable Git objects, and none of the omitted driver's original file bytes. No candidate has yet been translated with a model.

Evidence: local `experiments/native-suite-20261004/operator/<driver>/` control receipts, adapter receipts and seed audits. A path-free summary with receipt hashes is published in the environment repository under `validation/local-20261004.json`. Raw operator material is not a worker input.

Offline engineering checks:

- 37 focused native-suite, public-test, platform, generated-suite and worker checks passed. These are synthetic/local executor tests, not hardware validation.
- Ruff lint and format checks passed for the changed Python files.
- The complete repository pytest invocation finished with 16 failures. They concern removed environment arguments in older bootstrap/CLI fixtures, model-call counts in old repair scenarios, and an exact prompt-string assertion. This does not constitute a passing full regression suite. The relevant old interface fixtures in `test_platform_execution.py` and `test_platform_worker.py` were updated without changing production behavior to satisfy them; the other historical failures remain.
- Repository-wide Ruff also reports existing lint/format debt across unrelated modules. No bulk reformat or unrelated controller changes were made.

Operational limits:

- Vsock excluded because the preparation host lacks `/dev/vhost-vsock`.
- No private assertions, independent blind evaluation or performance threshold study was performed.
- Git history isolation is verified; host-level denial of access to operator repositories requires separate worker deployment. Development mode is not an adversarial security boundary.
- Docker and existing download caches are shared; compiler intermediates were removed after the controls. WSL filesystem deletion did not reclaim the Windows VHDX allocation; D: still had about 5.2 GB free.
