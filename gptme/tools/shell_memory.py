"""Memory guard: keep commands the agent starts from exhausting system memory.

Several heavy commands at once (parallel renders, model inference, big
builds) can run a machine out of memory; on macOS that can end in a kernel
panic and forced reboot. Per-process limits (``GPTME_SHELL_MEMORY_LIMIT``,
``ulimit -v``) don't catch that, and are not enforced on macOS at all.

The guard watches system memory from a daemon thread. When it runs low, it
kills the process group the agent started that uses the most memory (the
persistent shell's running command, or a background job) and tells the
model why, through that command's output or the job's completion message.

It only acts on groups holding a meaningful share of RAM, so it never kills
agent commands for memory pressure the agent didn't cause. Disable with
``GPTME_SHELL_MEMORY_GUARD=0``.
"""

import contextlib
import ctypes
import functools
import logging
import os
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Memory is low when less than this share of RAM is available.
LOW_AVAILABLE = 0.10
# Only kill a group holding at least this share of RAM (not the agent's fault
# otherwise: a browser or VM is using the memory).
MIN_KILL_SHARE = 0.05
INTERVAL = 1.0
# After a kill, give the system time to reclaim memory before acting again.
COOLDOWN = 5.0

# macOS kern.memorystatus_vm_pressure_level: 1 normal, 2 warn, 4 critical
_MACOS_PRESSURE_WARN = 2


@dataclass
class Target:
    """A process group the agent started."""

    pgid: int
    label: str  # e.g. "background job #3"
    kill: Callable[[], None]
    alive: Callable[[], bool]
    # receives the explanation for the model
    on_killed: Callable[[str], None]


@functools.cache
def _libc():
    import ctypes.util

    return ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)


def _sysctl_int(name: str) -> int | None:
    try:
        libc = _libc()
        value = ctypes.c_int64(0)
        size = ctypes.c_size_t(ctypes.sizeof(value))
        if libc.sysctlbyname(
            name.encode(), ctypes.byref(value), ctypes.byref(size), None, 0
        ):
            return None
    except (OSError, AttributeError):
        return None
    return value.value  # little-endian: a 4-byte int lands in the low bits


def system_memory() -> tuple[int, float, bool] | None:
    """(total bytes, available share, kernel reports pressure), or None if unknown."""
    if sys.platform == "darwin":
        total = _sysctl_int("hw.memsize")
        free_pct = _sysctl_int("kern.memorystatus_level")
        pressure = _sysctl_int("kern.memorystatus_vm_pressure_level")
        if not total or free_pct is None:
            return None
        return total, free_pct / 100, (pressure or 0) >= _MACOS_PRESSURE_WARN
    try:
        info: dict[str, int] = {}
        with open("/proc/meminfo") as f:
            for line in f:
                key, _, rest = line.partition(":")
                if key in ("MemTotal", "MemAvailable"):
                    info[key] = int(rest.split()[0]) * 1024
        total, available = info["MemTotal"], info["MemAvailable"]
    except (OSError, KeyError, ValueError, IndexError):
        return None
    return total, available / total, False


def group_rss() -> dict[int, int]:
    """Resident memory in bytes per process group."""
    try:
        out = subprocess.run(
            ["ps", "-A", "-o", "pgid=,rss="],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return {}
    rss: dict[int, int] = {}
    for line in out.splitlines():
        with contextlib.suppress(ValueError):
            pgid, kib = (int(x) for x in line.split())
            rss[pgid] = rss.get(pgid, 0) + kib * 1024
    return rss


def _gb(n: int) -> str:
    return f"{n / 1024**3:.1f} GB"


class MemoryGuard:
    def __init__(
        self,
        memory: Callable[[], tuple[int, float, bool] | None] = system_memory,
        rss: Callable[[], dict[int, int]] = group_rss,
    ) -> None:
        self._memory = memory
        self._rss = rss
        self._targets: dict[int, Target] = {}
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._last_kill = 0.0

    def register(self, target: Target) -> None:
        with self._lock:
            self._targets[target.pgid] = target
            if self._thread is None:
                self._thread = threading.Thread(
                    target=self._run, name="gptme-memory-guard", daemon=True
                )
                self._thread.start()

    def _run(self) -> None:
        while True:
            time.sleep(INTERVAL)
            try:
                self.check()
            except Exception:  # the guard must never take the agent down
                logger.exception("memory guard check failed")

    def check(self) -> Target | None:
        """Kill the largest agent group if memory is low; return it, if any."""
        with self._lock:
            for pgid in [p for p, t in self._targets.items() if not t.alive()]:
                del self._targets[pgid]
            targets = list(self._targets.values())
        if not targets or time.monotonic() - self._last_kill < COOLDOWN:
            return None
        if (memory := self._memory()) is None:
            return None
        total, available, pressure = memory
        if available >= LOW_AVAILABLE and not pressure:
            return None
        rss = self._rss()
        target = max(targets, key=lambda t: rss.get(t.pgid, 0))
        used = rss.get(target.pgid, 0)
        if used < MIN_KILL_SHARE * total:
            return None
        self._last_kill = time.monotonic()
        reason = (
            f"Killed {target.label}: system memory was nearly exhausted "
            f"({available:.0%} of {_gb(total)} available) and it was using "
            f"{_gb(used)}. Run fewer memory-heavy commands at once."
        )
        logger.warning(reason)
        target.on_killed(reason)
        target.kill()
        return target


_guard: MemoryGuard | None = None
_guard_lock = threading.Lock()


def kill_tree(pids: list[int]) -> None:
    """SIGTERM then SIGKILL a fixed list of processes.

    The list is taken once: re-listing children after SIGTERM would miss a
    worker that ignores it and was re-parented when its parent exited.
    """
    for sig in (signal.SIGTERM, signal.SIGKILL):
        for pid in pids:
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.kill(pid, sig)
        if sig == signal.SIGTERM:
            time.sleep(0.2)


def kill_group(pgid: int) -> None:
    """SIGTERM then SIGKILL every process in a group, leader or not."""
    for sig in (signal.SIGTERM, signal.SIGKILL):
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(pgid, sig)
        if sig == signal.SIGTERM:
            time.sleep(0.2)


def group_alive(pgid: int) -> bool:
    """Whether any process is left in the group (its leader may be gone)."""
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def guard_enabled() -> bool:
    if os.name != "posix":
        return False
    from ..config import get_config

    return bool(get_config().get_env_bool("SHELL_MEMORY_GUARD", default=True))


def watch(target: Target) -> None:
    """Have the memory guard watch a process group the agent started."""
    global _guard
    try:
        if not guard_enabled():
            return
    except Exception:  # config errors must not break running commands
        logger.debug("memory guard: could not read config", exc_info=True)
        return
    with _guard_lock:
        if _guard is None:
            _guard = MemoryGuard()
    _guard.register(target)
