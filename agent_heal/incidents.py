"""JSONL incident log — the supervisor's flight recorder.

Every supervised attempt appends one fsync'd record, so the incident history
survives the same infra deaths the supervisor heals. Powers post-mortems and
the taxonomy's recovery-rate learning.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, asdict
from typing import Any


@dataclass
class Incident:
    job_id: str
    attempt: int
    failure: str            # taxonomy name, "none" on clean completion
    policy: str             # recovery policy chosen
    exit_code: int | None
    cpu_s: float            # child CPU seconds this attempt (wait4 rusage)
    wall_s: float           # wall seconds this attempt
    recovered: bool         # did the job eventually complete?
    evidence: str = ""
    ts: float = 0.0

    def __post_init__(self):
        if not self.ts:
            self.ts = time.time()


class IncidentLog:
    def __init__(self, path: str):
        self.path = path
        d = os.path.dirname(os.path.abspath(path))
        os.makedirs(d, exist_ok=True)

    def append(self, incident: Incident) -> None:
        with open(self.path, "a") as f:
            f.write(json.dumps(asdict(incident)) + "\n")
            f.flush()
            os.fsync(f.fileno())

    def read_all(self) -> list[dict[str, Any]]:
        if not os.path.exists(self.path):
            return []
        out = []
        with open(self.path) as f:
            for line in f:
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return out
