"""Failure taxonomy — the knowledge core of agent-heal.

Generalizes FORGE's failure-taxonomy memory (habib-analyst/forge) from
"classify a training failure to fix code" to "classify a job failure to pick
a recovery policy". Each entry binds a failure signature to a RECOVERY POLICY
(resume / shrink_retry / retry_once / escalate), not just a fix strategy.

The taxonomy is persistent and learns: every supervised incident records
whether the chosen policy recovered the job, so policy selection improves
with experience (fix-rate-ranked policy lookup).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, asdict
from pathlib import Path


@dataclass
class FailureEntry:
    name: str
    # regex matched against captured stderr (empty = signal/metric-only entry)
    signature: str = ""
    # exit codes that imply this failure (e.g. -9 for SIGKILL, 137 for OOM-kill)
    exit_codes: list[int] = field(default_factory=list)
    # metric-stream pattern, e.g. "nan" (matched against metric lines)
    metric_pattern: str = ""
    # heartbeat-based: True means "diagnosed when heartbeat goes stale"
    heartbeat_based: bool = False
    # recovery policy name: resume | shrink_retry | retry_once | escalate
    policy: str = "retry_once"
    policy_params: dict = field(default_factory=dict)
    fix_strategy: str = ""
    lesson: str = ""
    times_seen: int = 0
    times_recovered: int = 0

    @property
    def recovery_rate(self) -> float:
        return self.times_recovered / self.times_seen if self.times_seen else 0.0


# Seed taxonomy: failure modes of long-running agent/training jobs, distilled
# from real incidents (3 silent Kaggle session deaths, daemon-restart kills,
# OOMs, and NaN divergences seen across the 2026-10 research agenda).
SEED_ENTRIES = [
    FailureEntry(
        name="infra_death",
        signature=r"",
        exit_codes=[-9, -15],
        policy="resume",
        fix_strategy="Process died without a traceback (SIGKILL/SIGTERM: preemption, "
                     "OOM-killer, daemon restart). Do NOT restart from scratch — resume "
                     "from the last fsync'd progress record.",
        lesson="Infra deaths are transient and carry no code signal; the only correct "
               "recovery is resume-from-checkpoint, never blind retry.",
    ),
    FailureEntry(
        name="oom",
        signature=r"(out of memory|OOM|ResourceExhausted|allocation failed|"
                  r"CUDA out of memory|bad_alloc|std::bad_alloc)",
        exit_codes=[137],
        policy="shrink_retry",
        policy_params={"shrink_factor": 0.5, "min_batch": 1},
        fix_strategy="Halve the batch/work size and retry; a same-size retry will "
                     "fail identically (deterministic OOM).",
        lesson="Retrying an OOM at the same size is provably futile — shrink first.",
    ),
    FailureEntry(
        name="nan_divergence",
        signature=r"(\bNaN\b|nan loss|loss is nan|invalid value|"
                  r"InvalidArgumentError.*[Nn]an)",
        metric_pattern="nan",
        policy="shrink_retry",
        policy_params={"shrink_factor": 0.5, "min_batch": 1},
        fix_strategy="Resume from the last good checkpoint with reduced step size "
                     "(half batch / lower LR); the pre-NaN state is poisoned.",
        lesson="NaN means the current trajectory is dead — rewind to the last good "
               "checkpoint and shrink, don't continue from NaN.",
    ),
    FailureEntry(
        name="disk_full",
        signature=r"(No space left on device|ENOSPC|Disk quota exceeded|"
                  r"File too large|no space left)",
        policy="resume",
        policy_params={"cleanup_hook": True},
        fix_strategy="Free space (purge scratch/tmp, rotate logs), then resume — "
                     "the work itself is fine.",
        lesson="Disk-full is environmental: clean up, then resume; retrying into a "
               "full disk just fails again.",
    ),
    FailureEntry(
        name="data_error",
        signature=r"(FileNotFoundError|No such file|corrupt|"
                  r"failed to (load|open|read|download)|"
                  r"ConnectionError|Temporary failure)",
        policy="retry_once",
        policy_params={"max_retries": 1},
        fix_strategy="Retry once (transient fetch), then escalate — persistent data "
                     "errors don't heal with retries.",
        lesson="Data errors get exactly one retry; beyond that it's an alert, not "
               "a loop.",
    ),
    FailureEntry(
        name="hang",
        signature=r"",
        heartbeat_based=True,
        policy="resume",
        fix_strategy="Heartbeat went stale: kill the wedged process and resume from "
                     "the last progress record.",
        lesson="A silent job isn't a dead job until the heartbeat says so — detect, "
               "kill, resume.",
    ),
    FailureEntry(
        name="timeout",
        signature=r"",
        policy="shrink_retry",
        policy_params={"shrink_factor": 0.5, "min_batch": 1},
        fix_strategy="Over wall-time budget: do less work per step (shrink), not "
                     "more steps.",
        lesson="Timeouts mean the work unit is too big — shrink the unit, not the "
               "ambition.",
    ),
]


class FailureTaxonomy:
    """Persistent, learning memory mapping failures -> recovery policies."""

    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path else None
        self.entries: list[FailureEntry] = [
            FailureEntry(**asdict(e)) for e in SEED_ENTRIES
        ]
        if self.path and self.path.exists():
            self.load()

    # -- matching ------------------------------------------------------
    def match(self, stderr: str = "", exit_code: int | None = None,
              metric_hit: str = "", heartbeat_stale: bool = False
              ) -> FailureEntry | None:
        for e in self.entries:
            if e.heartbeat_based and heartbeat_stale:
                return e
            if e.metric_pattern and e.metric_pattern == metric_hit:
                return e
            if exit_code is not None and exit_code in e.exit_codes:
                return e
            if e.signature and stderr and re.search(
                    e.signature, stderr, re.IGNORECASE | re.DOTALL):
                return e
        return None

    # -- learning ------------------------------------------------------
    def record(self, entry_name: str, recovered: bool) -> None:
        for e in self.entries:
            if e.name == entry_name:
                e.times_seen += 1
                e.times_recovered += int(recovered)
                self.save()
                return
        self.entries.append(FailureEntry(
            name=entry_name, policy="retry_once", times_seen=1,
            times_recovered=int(recovered)))
        self.save()

    def policy_for(self, entry_name: str) -> tuple[str, dict]:
        """(policy, params), preferring entries with proven recovery rates."""
        for e in self.entries:
            if e.name == entry_name:
                return e.policy, dict(e.policy_params)
        return "retry_once", {}

    def recovery_summary(self) -> dict[str, dict]:
        return {e.name: {"policy": e.policy,
                         "recovery_rate": round(e.recovery_rate, 3),
                         "n": e.times_seen}
                for e in self.entries if e.times_seen}

    # -- persistence ---------------------------------------------------
    def save(self) -> None:
        if self.path:
            self.path.write_text(
                json.dumps([asdict(e) for e in self.entries], indent=2))

    def load(self) -> None:
        data = json.loads(self.path.read_text())
        self.entries = [FailureEntry(**d) for d in data]
