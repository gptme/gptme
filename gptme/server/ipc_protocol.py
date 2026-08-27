"""IPC message protocol for gptme session daemon.

Wire format: 4-byte big-endian length prefix + UTF-8 JSON payload.
"""

from __future__ import annotations

import json
import struct
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Literal, cast

if TYPE_CHECKING:
    import socket

# Big-endian uint32 length prefix
_HEADER = struct.Struct("!I")

MessageType = Literal["input", "output", "status", "signal", "error"]
_MESSAGE_TYPES = frozenset(("input", "output", "status", "signal", "error"))


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
        try:
            decoded = json.loads(data)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("invalid IPC message JSON") from exc
        if not isinstance(decoded, dict):
            raise ValueError("IPC message must be a JSON object")

        message_type = decoded.get("type")
        if message_type not in _MESSAGE_TYPES:
            raise ValueError(f"invalid IPC message type: {message_type!r}")
        seq = decoded.get("seq", 0)
        if not isinstance(seq, int):
            raise ValueError("IPC message seq must be an integer")
        timestamp = decoded.get("timestamp", "")
        if not isinstance(timestamp, str):
            raise ValueError("IPC message timestamp must be a string")
        message_data = decoded.get("data", "")
        if not isinstance(message_data, (str, dict)):
            raise ValueError("IPC message data must be a string or object")

        return cls(
            type=cast("MessageType", message_type),
            data=message_data,
            seq=seq,
            timestamp=timestamp,
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
