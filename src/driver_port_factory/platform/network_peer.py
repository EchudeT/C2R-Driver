"""Public Ethernet oracle over a local QEMU socket; no translated driver logic."""

import hashlib
import json
import socket
import struct
import threading
from pathlib import Path

MAC = bytes.fromhex("525400123456")
PEER = bytes.fromhex("525400abcdef")


def request(sequence, length):
    return (
        PEER
        + MAC
        + b"\x88\xb5"
        + sequence.to_bytes(2, "big")
        + bytes((sequence + i * 17) & 255 for i in range(length - 16))
    )


def response(frame):
    return (
        frame[6:12] + frame[:6] + b"\x88\xb6" + frame[14:16] + bytes(b ^ 0xA5 for b in frame[16:])
    )


class WirePeer:
    def __init__(self, output):
        self.output = Path(output)
        self.output.mkdir(parents=True, exist_ok=True)
        self.frames = []
        self.errors = []
        self.connected = False
        self.closed = False
        self.connection = None
        self.stopping = threading.Event()
        self.listener = socket.socket()
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(1)
        self.listener.settimeout(0.2)
        self.port = self.listener.getsockname()[1]
        self.thread = threading.Thread(target=self.serve, daemon=True)
        self.thread.start()

    def event(self, kind, **facts):
        with (self.output / "wire-events.jsonl").open("a") as stream:
            stream.write(json.dumps({"kind": kind, **facts}) + "\n")

    def exact(self, count):
        data = bytearray()
        while len(data) < count:
            part = self.connection.recv(count - len(data))
            if not part:
                if data:
                    raise ValueError("truncated QEMU wire frame")
                raise EOFError
            data.extend(part)
        return bytes(data)

    def serve(self):
        try:
            while not self.stopping.is_set():
                try:
                    self.connection, _ = self.listener.accept()
                    break
                except TimeoutError:
                    continue
            if self.connection is None:
                return
            self.connected = True
            self.event("connected")
            with self.connection:
                while not self.stopping.is_set():
                    length = struct.unpack("!I", self.exact(4))[0]
                    if not 14 <= length <= 65536:
                        raise ValueError("invalid Ethernet wire length")
                    frame = self.exact(length)
                    if frame[12:14] != b"\x88\xb5":
                        self.event("background_frame", length=length)
                        continue
                    seq = int.from_bytes(frame[14:16], "big")
                    valid = length >= 16 and frame == request(seq, length)
                    row = {
                        "sequence": seq,
                        "length": length,
                        "valid": valid,
                        "sha256": hashlib.sha256(frame).hexdigest(),
                    }
                    self.frames.append(row)
                    self.event("request", **row)
                    if not valid:
                        self.errors.append(f"wire content mismatch sequence {seq}")
                        continue
                    replies = (
                        [response(request(n, 60)) for n in range(2000, 2016)]
                        if seq == 2000
                        else [response(frame)]
                    )
                    self.connection.sendall(
                        b"".join(struct.pack("!I", len(reply)) + reply for reply in replies)
                    )
                    self.event("response", sequence=seq, count=len(replies))
        except EOFError:
            self.event("disconnected")
        except (OSError, ValueError, struct.error) as error:
            if not self.stopping.is_set():
                self.errors.append(str(error))
                self.event("error", error=str(error))

    def close(self):
        if self.closed:
            return
        self.closed = True
        # QEMU has exited before this call on the normal path; let the reader drain.
        self.thread.join(timeout=1)
        self.stopping.set()
        if self.connection is not None:
            try:
                self.connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        self.listener.close()
        self.thread.join(timeout=1)
        (self.output / "wire.json").write_text(
            json.dumps(
                {"connected": self.connected, "frames": self.frames, "errors": self.errors},
                indent=2,
            )
            + "\n"
        )

    def assert_expected(self, expected):
        actual = [[f["sequence"], f["length"]] for f in self.frames]
        if (
            not self.connected
            or self.errors
            or actual != expected
            or not all(f["valid"] for f in self.frames)
        ):
            raise RuntimeError(
                f"WIRE_MISMATCH: expected={expected}, actual={actual}, "
                f"connected={self.connected}, errors={self.errors}"
            )
