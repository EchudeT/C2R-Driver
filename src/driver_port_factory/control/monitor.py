"""Read-only split terminal monitor; never opens the controller or validates its workflow."""

import argparse
import curses
import json
import math
import sqlite3
import sys
import time
import unicodedata
from collections import deque
from pathlib import Path
from types import SimpleNamespace

from .runtime import controller_status

TAIL_BYTES = 512 * 1024


def clean(text):
    # Logs are data: do not send terminal escapes or control characters to the terminal.
    return "".join(
        c if c == "\n" or not unicodedata.category(c).startswith("C") else " " for c in str(text)
    ).replace("\t", "    ")


def fit(text, width):
    result, used = [], 0
    for char in clean(text).replace("\n", " "):
        size = (
            0
            if unicodedata.combining(char)
            else (2 if unicodedata.east_asian_width(char) in {"W", "F"} else 1)
        )
        if used + size > width:
            break
        result.append(char)
        used += size
    return "".join(result)


def event_text(event):
    if not isinstance(event, dict):
        return ""
    kind = event.get("type", "event")
    item = event.get("item")
    if isinstance(item, dict):
        label = item.get("type", kind)
        body = item.get("text") or item.get("aggregated_output") or ""
        command = item.get("command")
        if command:
            body = f"$ {command}\n{body}"
        if not body:
            body = json.dumps(item, ensure_ascii=False)
        return clean(f"[{label} / {kind}]\n{body}")
    if kind == "error" or kind == "turn.failed":
        return clean(f"[{kind}] {json.dumps(event, ensure_ascii=False)}")
    if kind in {"thread.started", "turn.started", "turn.completed"}:
        return clean(f"[{kind}]")
    # Providers may emit text deltas without an item wrapper.
    if isinstance(event.get("delta"), str) or isinstance(event.get("text"), str):
        return clean(event.get("delta") or event.get("text"))
    return ""


class EventTail:
    def __init__(self):
        self.path = None
        self.identity = None
        self.offset = 0
        self.pending = b""
        self.lines = deque(maxlen=4000)

    def update(self, path):
        if path != self.path:
            self.path, self.identity, self.offset, self.pending = path, None, 0, b""
            self.lines.clear()
        if path is None or not path.exists():
            return list(self.lines)
        stat = path.stat()
        identity = (stat.st_dev, stat.st_ino)
        if self.identity != identity or stat.st_size < self.offset:
            self.lines.clear()
            self.pending = b""
            self.offset = max(0, stat.st_size - TAIL_BYTES)
            self.identity = identity
            with path.open("rb") as stream:
                stream.seek(self.offset)
                if self.offset:
                    stream.readline()  # Discard a partial historical record.
                    self.lines.append("[Showing recent output; full history remains in event log]")
                self.offset = stream.tell()
        with path.open("rb") as stream:
            stream.seek(self.offset)
            data = self.pending + stream.read(TAIL_BYTES)
            self.offset = stream.tell()
        records = data.split(b"\n")
        self.pending = records.pop()
        if len(self.pending) > 4 * TAIL_BYTES:
            self.pending = b""
            self.lines.append("[Oversized unfinished event omitted; inspect raw log]")
        for raw in records:
            try:
                text = event_text(json.loads(raw))
            except (ValueError, UnicodeError):
                text = "[Malformed event; inspect raw log]"
            if text:
                self.lines.extend(text.splitlines())
        return list(self.lines)


def snapshot(root):
    control = root / ".dpf"
    database = control / "run.sqlite3"
    with sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True, timeout=0.2) as db:
        stages = db.execute("SELECT name,status FROM stages ORDER BY position").fetchall()
    jobs = []
    for path in (control / "codex").glob("*.metrics.json"):
        try:
            value = json.loads(path.read_text())
            if isinstance(value, dict):
                jobs.append((path, value))
        except (OSError, ValueError):
            continue  # Atomic replacement/partial writes must not interrupt supervision.
    jobs.sort(key=lambda pair: (pair[1].get("started_at") or "", pair[0].name))
    return stages, jobs, controller_status(SimpleNamespace(control=control))["state"]


def selected_job(jobs, stage=None):
    return next(
        (pair for pair in reversed(jobs) if stage is None or pair[1].get("stage") == stage), None
    )


def platform_output(root, job):
    """Read bounded execution logs while a single model tool call is awaiting completion."""
    if not job or not job[1].get("job_id"):
        return []
    path = root / ".dpf/codex" / f"{job[1]['job_id']}.platform.json"
    try:
        value = json.loads(path.read_text())
        lines = [f"[Platform {value['action']}: {value['status']}] (no model polling)"]
        if value.get("error"):
            lines.append(clean(value["error"]))
        if value.get("log_root"):
            directory = Path(value["log_root"]).resolve()
            if not directory.is_relative_to(root.resolve()):
                return lines
            logs = sorted(directory.glob("*/*.bin"), key=lambda p: p.stat().st_mtime)[-2:]
            for log in logs:
                if not log.resolve().is_relative_to(root.resolve()):
                    continue
                with log.open("rb") as stream:
                    stream.seek(max(0, log.stat().st_size - 2000))
                    text = stream.read(2000).decode(errors="replace")
                lines.extend([str(log), *clean(text).splitlines()[-10:]])
        return lines
    except (OSError, ValueError, KeyError, TypeError):
        return []  # Concurrent atomic writes or removed files never trigger worker actions.


def columns(root, stages, job, state, lines, selected=None):
    left = [root.name, f"Controller: {state}", "Stages (durable state)", ""]
    for number, (name, status) in enumerate(stages, 1):
        marker = ">" if name == selected else " "
        shown = "STOPPED*" if status == "RUNNING" and state == "STOPPED" else status
        label = (
            "driver_implementation (incl. integration)" if name == "driver_implementation" else name
        )
        left.append(f"{marker}{number:02} {shown:16} {label}")
    right = ["Model output — waiting for a recorded call"]
    if job:
        path, info = job
        right = [
            f"{info.get('stage')} | {info.get('model') or 'model unknown'}",
            f"Call: {info.get('invocation_state', 'unknown')} | controller {state}",
            f"Log: {path.name.replace('.metrics.json', '.events.jsonl')}",
        ]
        estimate = info.get("estimate") or {}
        usd = estimate.get("usd")
        left += ["", f"Current call cost: {usd if usd is not None else 'unknown'} USD~"]
    right += [""] + (lines or ["No model event yet; stage RUNNING alone does not prove activity."])
    right += platform_output(root, job)
    return left, right


def draw(screen, left, right, selected_index, scroll):
    height, width = screen.getmaxyx()
    screen.erase()
    if width < 85 or height < 10:
        screen.addnstr(
            0, 0, "Enlarge terminal to at least 85 columns x 10 rows; q exits.", width - 1
        )
        screen.refresh()
        return
    split = min(65, max(35, width * 43 // 100))
    left_start = max(0, selected_index + 5 - (height - 3))
    content = right[4:]
    # Preserve whole logical lines; wrap with display-cell widths, including CJK output.
    wrapped = []
    for line in content:
        if not line:
            wrapped.append("")
        while line:
            part = fit(line, width - split - 3)
            if not part:
                break
            wrapped.append(part)
            line = line[len(part) :]
    visible = max(1, height - 6)
    end = max(visible, len(wrapped) - scroll)
    right_rows = right[:4] + wrapped[max(0, end - visible) : end]
    for row in range(height - 2):
        for col, limit, source in (
            (0, split - 1, left[left_start:]),
            (split + 1, width - split - 2, right_rows),
        ):
            if row < len(source):
                screen.addstr(row, col, fit(source[row], limit))
        screen.addch(row, split, "|")
    screen.addstr(
        height - 1,
        0,
        fit(
            "q quit | up/down select stage | f follow latest | PgUp/PgDn output | End live tail",
            width - 1,
        ),
    )
    screen.refresh()


def hide_cursor():
    try:
        curses.curs_set(0)
    except curses.error:
        pass


def run(screen, root, interval):
    hide_cursor()
    screen.timeout(100)
    tail, selected, scroll = EventTail(), None, 0
    last, data, error = 0.0, None, None
    while True:
        if time.monotonic() - last >= interval:
            try:
                stages, jobs, state = snapshot(root)
                job = selected_job(jobs, selected)
                event_path = (
                    job[0].with_name(job[0].name.replace(".metrics.json", ".events.jsonl"))
                    if job
                    else None
                )
                lines = tail.update(event_path)
                data = (stages, job, state, lines)
                error = None
            except (OSError, ValueError, sqlite3.Error) as problem:
                error = str(problem)
            last = time.monotonic()
        if data:
            stages, job, state, lines = data
            active = selected or (job[1].get("stage") if job else None)
            index = next((i for i, s in enumerate(stages) if s[0] == active), 0)
            left, right = columns(root, stages, job, state, lines, active)
            if error:
                right = ["Snapshot temporarily unavailable", clean(error), "", ""] + right
            draw(screen, left, right, index, scroll)
        else:
            draw(screen, [root.name], ["Waiting for workspace", error or "", "", ""], 0, 0)
        key = screen.getch()
        if key in (ord("q"), 27):
            return
        if key in (curses.KEY_DOWN, curses.KEY_UP) and data and stages:
            index = max(0, min(len(stages) - 1, index + (1 if key == curses.KEY_DOWN else -1)))
            selected, scroll, last = stages[index][0], 0, 0
        elif key == ord("f"):
            selected, scroll, last = None, 0, 0
        elif key == curses.KEY_PPAGE:
            scroll = min(4000, scroll + max(1, screen.getmaxyx()[0] - 6))
        elif key == curses.KEY_NPAGE:
            scroll = max(0, scroll - max(1, screen.getmaxyx()[0] - 6))
        elif key == curses.KEY_END:
            scroll = 0


def main():
    parser = argparse.ArgumentParser(description="Read-only stages / model output terminal monitor")
    parser.add_argument("workspace", type=Path)
    parser.add_argument("--interval", type=float, default=2)
    parser.add_argument("--once", action="store_true", help="print one snapshot without a terminal")
    args = parser.parse_args()
    if not math.isfinite(args.interval) or args.interval <= 0:
        parser.error("interval must be a positive finite number")
    root = args.workspace.expanduser().resolve()
    if args.once:
        stages, jobs, state = snapshot(root)
        job = selected_job(jobs)
        path = (
            job[0].with_name(job[0].name.replace(".metrics.json", ".events.jsonl")) if job else None
        )
        left, right = columns(root, stages, job, state, EventTail().update(path))
        for i in range(max(len(left), len(right))):
            print(
                f"{fit(left[i] if i < len(left) else '', 65):65} | "
                + fit(right[i] if i < len(right) else "", 100)
            )
        return
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        parser.error("interactive monitor requires a terminal; use --once for redirected output")
    try:
        curses.wrapper(run, root, args.interval)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
