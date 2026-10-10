"""Heartbeat mechanism for hang detection.

The job touches a heartbeat file every unit of work (or every N seconds);
the supervisor watches the file's mtime. A job that is alive but wedged
(infinite loop, deadlocked I/O, GPU hang) keeps its PID but stops
heartbeating — the only reliable signal is the stale heartbeat.
"""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path


class HeartbeatWriter:
    """For jobs: touch the heartbeat file each unit of work."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        if self.path.parent != Path("."):
            self.path.parent.mkdir(parents=True, exist_ok=True)

    def beat(self) -> None:
        self.path.touch()

    def periodic(self, interval_s: float, stop: threading.Event) -> None:
        """Run in a thread: beat every interval until stop is set."""
        while not stop.wait(interval_s):
            self.beat()


class HeartbeatMonitor:
    """For the supervisor: is the heartbeat stale?"""

    def __init__(self, path: str | Path, timeout_s: float):
        self.path = Path(path)
        self.timeout_s = timeout_s

    def age_s(self) -> float:
        try:
            return time.time() - self.path.stat().st_mtime
        except FileNotFoundError:
            # never beaten: only stale if the job should have started by now;
            # the supervisor seeds one beat at launch, so missing == stale.
            return float("inf")

    def is_stale(self) -> bool:
        return self.age_s() > self.timeout_s
