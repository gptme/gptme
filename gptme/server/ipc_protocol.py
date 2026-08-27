"""IPC message protocol for gptme session daemon.

Wire format: 4-byte big-endian length prefix + UTF-8 JSON payload.
"""

from __future__ import annotations

import json
import struct
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    import socket

# Big-endian uint32 length prefix
_HEADER = struct.Struct("!I")

MessageType = Literal["input", "output", "status", "signal", "error"]


@dataclass
class IPCMessage:
    type: MessageType
    data: str | dict = ""
    seq: int = 0
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def encode(self) -> bytes:
        payload = json.dumps(
            {
                "type": self.type,
                "data": self.data,
                "seq": self.seq,
                "timestamp": self.timestamp,
            }
        ).encode()
        return _HEADER.pack(len(payload)) + payload

    @classmethod
    def from_bytes(cls, data: bytes) -> IPCMessage:
        d = json.loads(data)
        return cls(
            type=d["type"],
            data=d.get("data", ""),
            seq=d.get("seq", 0),
            timestamp=d.get("timestamp", ""),
        )


def send_msg(sock: socket.socket, msg: IPCMessage) -> None:
    sock.sendall(msg.encode())


def recv_msg(sock: socket.socket) -> IPCMessage | None:
    """Receive one framed message; returns None on EOF."""
    header = _recv_exact(sock, _HEADER.size)
    if not header:
        return None
    (length,) = _HEADER.unpack(header)
    payload = _recv_exact(sock, length)
    if not payload:
        return None
    return IPCMessage.from_bytes(payload)


def _recv_exact(sock: socket.socket, n: int) -> bytes:
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            return b""
        buf.extend(chunk)
    return bytes(buf)
