# Public native experiment tools

These scripts provide one environment and upstream-test runner for DPF and Codex.
No paid model is invoked by preparation or checking.

`package_tests.py` adapts the mechanical packaging script from
`driver-port-lab/src/driver_port_lab/adapters/fixtures/prepare_native_tests.py`.
The baseline export and test-summary interpretation reuse the v2 experiment approach;
the hollow baseline, lazy trial creation and single-ISO runtime are implemented here.
Guest assertions and traffic come from pinned Asterinas files, not new test programs.

Entrypoint: `../native-experiment.py`. See
[the experiment protocol](../../docs/experiments/NATIVE_DRIVER_SUITE.zh-CN.md).

`runtime.py` reuses the production `platform/guest.py` serial/QMP transport.
The original iperf host scripts run only inside their disposable Docker PID/network
namespace because their cleanup kills visible QEMU processes.
