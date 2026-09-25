from pathlib import Path

from driver_port_factory.codex.contracts import CodexContinuation
from driver_port_factory.migration.implementation_preflight import (
    format_findings,
    inspect_implementation,
)
from driver_port_factory.port import PortRunner


class _Project:
    """The preflight intentionally does not inspect driver names or APIs."""


def _outputs(tmp_path: Path, runtime: bytes, script: str) -> Path:
    output = tmp_path / ".dpf-output"
    output.mkdir()
    (output / "runtime-artifact").write_bytes(runtime)
    (output / "implementation-smoke.sh").write_text(script)
    return output


def test_preflight_reports_only_high_confidence_marker_and_exact_locations(tmp_path):
    output = _outputs(
        tmp_path,
        b"Asterinas e1000 implementation snapshot\nPCI match: 8086:100e\n",
        '#!/bin/sh\nqemu-system-x86_64 -S -kernel "$DPF_RUNTIME_ARTIFACT"\n',
    )
    finding = inspect_implementation(_Project(), tmp_path, "base")
    assert finding["status"] == "FAIL"
    assert {(item["code"], item["line"]) for item in finding["findings"]} == {
        ("non-bootable-runtime-marker", 1),
        ("qemu-paused", 2),
    }
    message = format_findings(finding)
    assert str(output / "runtime-artifact") + ":1" in message
    assert str(output / "implementation-smoke.sh") + ":2" in message
    assert "not evidence of a bootable" in message
    assert "contains no continuation/QMP command" in message
    assert "source: 'Asterinas e1000 implementation snapshot'" in message


def test_preflight_does_not_reject_valid_text_package_or_loopback_api(tmp_path):
    _outputs(
        tmp_path,
        b"#!/bin/sh\necho implementation\n",
        '#!/bin/sh\nqemu-system-x86_64 -kernel "$DPF_RUNTIME_ARTIFACT"\n',
    )
    # A source API name is deliberately irrelevant to this generic preflight.
    (tmp_path / "driver.rs").write_text("Loopback::new();\n")
    finding = inspect_implementation(_Project(), tmp_path, "base")
    assert finding["status"] == "PASS"
    assert finding["findings"] == []


def test_preflight_does_not_use_marker_words_as_a_driver_api_rule(tmp_path):
    _outputs(
        tmp_path,
        b"#!/bin/sh\n# implementation snapshot marker for a documented config\n",
        '#!/bin/sh\nqemu-system-x86_64 -append "$DPF_RUNTIME_ARTIFACT"\n',
    )
    finding = inspect_implementation(_Project(), tmp_path, "base")
    assert finding["status"] == "PASS"


def test_preflight_allows_qemu_paused_until_qmp_continuation(tmp_path):
    _outputs(
        tmp_path,
        b"\x7fELF\x02\x01binary",
        '#!/bin/sh\nqemu-system-x86_64 -S -kernel "$DPF_RUNTIME_ARTIFACT"\nqmp-cont\n',
    )
    finding = inspect_implementation(_Project(), tmp_path, "base")
    assert finding["status"] == "PASS"


def test_preflight_ignores_comments_and_echoed_examples(tmp_path):
    _outputs(
        tmp_path,
        b"Asterinas implementation snapshot marker\nPCI match: documented\n",
        '#!/bin/sh\n'
        '# qemu-system-x86_64 -S -kernel "$DPF_RUNTIME_ARTIFACT"\n'
        'echo "qmp cont -kernel $DPF_RUNTIME_ARTIFACT"\n'
        'qemu-system-x86_64 -kernel "$DPF_RUNTIME_ARTIFACT"\n',
    )
    finding = inspect_implementation(_Project(), tmp_path, "base")
    assert finding["status"] == "FAIL"
    assert {item["code"] for item in finding["findings"]} == {
        "non-bootable-runtime-marker"
    }


def test_preflight_reports_missing_binding_at_active_qemu_line(tmp_path):
    _outputs(
        tmp_path,
        b"#!/bin/sh\necho implementation snapshot marker\nPCI match\n",
        '#!/bin/sh\n'
        '# DPF_RUNTIME_ARTIFACT appears only in documentation\n'
        'qemu-system-x86_64 -kernel /tmp/other-kernel\n',
    )
    finding = inspect_implementation(_Project(), tmp_path, "base")
    assert finding["status"] == "FAIL"
    binding = next(item for item in finding["findings"] if item["code"] == "runtime-not-bound")
    assert binding["line"] == 3
    assert binding["text"].startswith("qemu-system-x86_64")


def test_preflight_does_not_treat_comment_qmp_as_continuation(tmp_path):
    _outputs(
        tmp_path,
        b"\x7fELF\x02\x01binary",
        '#!/bin/sh\n'
        'qemu-system-x86_64 -S -kernel "$DPF_RUNTIME_ARTIFACT"\n'
        '# qmp-cont is documented here, but never executed\n',
    )
    finding = inspect_implementation(_Project(), tmp_path, "base")
    paused = next(item for item in finding["findings"] if item["code"] == "qemu-paused")
    assert paused["line"] == 2
    assert "no continuation/QMP command" in paused["detail"]
    assert "-S" in format_findings(finding)


def test_preflight_does_not_treat_qemu_append_text_as_continuation(tmp_path):
    _outputs(
        tmp_path,
        b"\x7fELF\x02\x01binary",
        '#!/bin/sh\n'
        'qemu-system-x86_64 -S -kernel "$DPF_RUNTIME_ARTIFACT" '
        '-append "continue"\n',
    )
    finding = inspect_implementation(_Project(), tmp_path, "base")
    assert any(item["code"] == "qemu-paused" for item in finding["findings"])


def test_preflight_accepts_qmp_json_continuation_command(tmp_path):
    _outputs(
        tmp_path,
        b"\x7fELF\x02\x01binary",
        '#!/bin/sh\n'
        'qemu-system-x86_64 -S -kernel "$DPF_RUNTIME_ARTIFACT"\n'
        'printf \'{"execute":"cont"}\\n\' | socat - UNIX-CONNECT:/tmp/qmp\n',
    )
    finding = inspect_implementation(_Project(), tmp_path, "base")
    assert finding["status"] == "PASS"


def test_preflight_checks_qemu_nested_in_shell_command(tmp_path):
    _outputs(
        tmp_path,
        b"Asterinas implementation snapshot\nPCI match: 8086:100e\n",
        '#!/bin/sh\n'
        "sh -c 'timeout 3s qemu-system-x86_64 -S "
        '-drive file="$DPF_RUNTIME_ARTIFACT",format=raw'"'\n",
    )
    finding = inspect_implementation(_Project(), tmp_path, "base")
    assert finding["status"] == "FAIL"
    assert {item["code"] for item in finding["findings"]} == {
        "non-bootable-runtime-marker",
        "qemu-paused",
    }


def test_preflight_does_not_treat_echoed_nested_qemu_as_execution(tmp_path):
    _outputs(
        tmp_path,
        b"Asterinas implementation snapshot\nPCI match: documentation\n",
        '#!/bin/sh\n'
        "sh -c 'echo qemu-system-x86_64 -kernel "
        '$DPF_RUNTIME_ARTIFACT'"'\n",
    )
    finding = inspect_implementation(_Project(), tmp_path, "base")
    assert finding["status"] == "PASS"


def test_continuation_guard_preserves_workspace_location_but_ignores_attempt_id():
    first = PortRunner._continuation_detail(CodexContinuation(
        "failed: /tmp/run/.dpf/implementation-smoke/11111111111111111111111111111111/receipt.json"
    ))
    second = PortRunner._continuation_detail(CodexContinuation(
        "failed: /tmp/run/.dpf/implementation-smoke/22222222222222222222222222222222/receipt.json"
    ))
    assert first == second
    assert "/tmp/run/.dpf/implementation-smoke/<attempt>/receipt.json" in first
