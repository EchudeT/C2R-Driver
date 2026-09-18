from __future__ import annotations

import sys
from pathlib import Path

from driver_port_factory.codex.transport import execute


def test_checkpoint_is_persisted_before_process_completes(tmp_path: Path):
    marker = tmp_path / "checkpoint"
    prompt = "测试" * 100000
    script = (
        "import pathlib,sys,time\n"
        f"assert len(sys.stdin.read()) == {len(prompt)}\n"
        "sys.stderr.write('diagnostic ' * 10000)\n"
        "print('{\"type\":\"thread.started\",\"thread_id\":\"persistent\"}',flush=True)\n"
        f"marker=pathlib.Path({str(marker)!r})\n"
        "deadline=time.monotonic()+5\n"
        "while not marker.exists() and time.monotonic()<deadline: time.sleep(.01)\n"
        "assert marker.read_text()=='persistent'\n"
    )
    result = execute(
        [sys.executable, "-c", script], cwd=tmp_path, prompt=prompt,
        on_event=lambda event: marker.write_text(event["thread_id"]),
    )
    assert result.returncode == 0
    assert len(result.stderr) <= 8000
