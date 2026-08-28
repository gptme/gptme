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
# Cap the declared payload so a peer cannot force a multi-gigabyte buffer.
MAX_IPC_PAYLOAD = 1024 * 1024

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
        if len(payload) > MAX_IPC_PAYLOAD:
            raise ValueError(f"IPC payload too large: {len(payload)} bytes")
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


def recv_msg(sock: socket.socket, buffer: bytearray | None = None) -> IPCMessage | None:
    """Receive one framed message; returns None on EOF.

    Pass a persistent ``buffer`` when the socket has a timeout. Bytes received
    before a timeout then remain available to the next call instead of being
    mistaken for a new frame header.
    """
    if buffer is None:
        header = _recv_exact(sock, _HEADER.size)
        if not header:
            return None
        length = _checked_payload_length(header)
        payload = _recv_exact(sock, length)
        if not payload:
            return None
        return IPCMessage.from_bytes(payload)

    while len(buffer) < _HEADER.size:
        chunk = sock.recv(_HEADER.size - len(buffer))
        if not chunk:
            if buffer:
                raise ValueError("incomplete IPC message header")
            return None
        buffer.extend(chunk)
    length = _checked_payload_length(buffer[: _HEADER.size])
    frame_size = _HEADER.size + length
    while len(buffer) < frame_size:
        chunk = sock.recv(frame_size - len(buffer))
        if not chunk:
            raise ValueError("incomplete IPC message payload")
        buffer.extend(chunk)
    payload = bytes(buffer[_HEADER.size : frame_size])
    del buffer[:frame_size]
    return IPCMessage.from_bytes(payload)


def _checked_payload_length(header: bytes | bytearray) -> int:
    (length,) = _HEADER.unpack(header)
    if length > MAX_IPC_PAYLOAD:
        raise ValueError(f"IPC payload too large: {length} bytes")
    return length


def _recv_exact(sock: socket.socket, n: int) -> bytes:
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            return b""
        buf.extend(chunk)
    return bytes(buf)
