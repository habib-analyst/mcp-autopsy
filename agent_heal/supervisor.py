"""The supervisor loop: run a job, watch it, classify failures, recover.

For each attempt the supervisor:
  1. launches the job as a subprocess (new process group, so a hang kill
     takes the whole tree),
  2. seeds + monitors the heartbeat file (hang detection),
  3. tails stdout for NaN metric lines (divergence can be caught live),
  4. on exit (or kill) measures child CPU via RUSAGE_CHILDREN delta,
  5. classifies the failure, selects the recovery policy from the taxonomy,
  6. applies it (resume / shrink+resume / single retry / escalate),
  7. appends an fsync'd incident record.

Environment contract with the job (see checkpoint.py):
  AGENT_HEAL_PROGRESS  path of the fsync'd progress JSONL
  AGENT_HEAL_HEARTBEAT  path of the heartbeat file
  AGENT_HEAL_ATTEMPT    0-based attempt number (fault injection uses this)
  AGENT_HEAL_SHRINK     shrink factor for this attempt (1.0, 0.5, ...)
"""

from __future__ import annotations

import os
import resource
import signal
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from .taxonomy import FailureTaxonomy
from .classifier import classify, scan_metrics
from .policies import decide
from .heartbeat import HeartbeatMonitor
from .incidents import Incident, IncidentLog


@dataclass
class SupervisionResult:
    recovered: bool
    attempts: int
    total_cpu_s: float
    total_wall_s: float
    incidents: list[Incident] = field(default_factory=list)
    final_failure: str | None = None


def _child_cpu() -> float:
    ru = resource.getrusage(resource.RUSAGE_CHILDREN)
    return ru.ru_utime + ru.ru_stime


class Supervisor:
    def __init__(self, taxonomy_path: str | Path | None = None,
                 incident_log: str | Path = "incidents.jsonl",
                 max_attempts: int = 5,
                 hang_timeout_s: float = 5.0,
                 attempt_timeout_s: float = 600.0,
                 backoff_base: float = 2.0,
                 poll_s: float = 0.2):
        self.taxonomy = FailureTaxonomy(taxonomy_path)
        self.incidents = IncidentLog(str(incident_log))
        self.max_attempts = max_attempts
        self.hang_timeout_s = hang_timeout_s
        self.attempt_timeout_s = attempt_timeout_s
        self.backoff_base = backoff_base
        self.poll_s = poll_s

    # -- public ------------------------------------------------------
    def run(self, cmd: list[str], job_id: str, run_dir: str | Path,
            extra_env: dict | None = None) -> SupervisionResult:
        run_dir = Path(run_dir)
        run_dir.mkdir(parents=True, exist_ok=True)
        progress = run_dir / "progress.jsonl"
        heartbeat = run_dir / "heartbeat"
        incidents: list[Incident] = []
        total_cpu = 0.0
        t_start = time.monotonic()
        shrink = 1.0
        env_extra = dict(extra_env or {})

        for attempt in range(self.max_attempts + 1):
            stdout_f = run_dir / f"stdout_attempt{attempt}.log"
            stderr_f = run_dir / f"stderr_attempt{attempt}.log"
            heartbeat.touch()  # seed: a starting job is not a hung job
            env = dict(os.environ)
            env.update(env_extra)
            env["AGENT_HEAL_PROGRESS"] = str(progress)
            env["AGENT_HEAL_HEARTBEAT"] = str(heartbeat)
            env["AGENT_HEAL_ATTEMPT"] = str(attempt)
            env["AGENT_HEAL_SHRINK"] = str(shrink)

            cpu_before = _child_cpu()
            w0 = time.monotonic()
            proc = subprocess.Popen(
                cmd, stdout=open(stdout_f, "w"), stderr=open(stderr_f, "w"),
                env=env, start_new_session=True)
            monitor = HeartbeatMonitor(heartbeat, self.hang_timeout_s)
            death = self._watch(proc, monitor, stdout_f, w0)
            rc = proc.wait()
            wall = time.monotonic() - w0
            cpu = _child_cpu() - cpu_before
            total_cpu += cpu

            stdout_text = stdout_f.read_text(errors="replace")
            stderr_text = stderr_f.read_text(errors="replace")
            metric_lines = [ln for ln in stdout_text.splitlines()
                            if "metric" in ln.lower()]
            hb_stale = death == "hang"

            if rc == 0 and not hb_stale and not scan_metrics(metric_lines):
                inc = Incident(job_id, attempt, "none", "none", rc,
                               round(cpu, 3), round(wall, 3), True)
                self.incidents.append(inc)
                incidents.append(inc)
                self.taxonomy.record("clean_run", True)
                return SupervisionResult(True, attempt + 1,
                                         round(total_cpu, 3),
                                         round(time.monotonic() - t_start, 3),
                                         incidents)

            c = classify(exit_code=rc if death != "hang" else None,
                         stderr=stderr_text, metric_lines=metric_lines,
                         heartbeat_stale=hb_stale,
                         timed_out=(death == "timeout"),
                         taxonomy=self.taxonomy)
            decision = decide(c.policy, c.policy_params, attempt,
                              self.max_attempts, self.backoff_base)
            inc = Incident(job_id, attempt, c.name, c.policy, rc,
                           round(cpu, 3), round(wall, 3), False, c.evidence)
            self.incidents.append(inc)
            incidents.append(inc)

            if decision.escalate:
                self.taxonomy.record(c.name, recovered=False)
                return SupervisionResult(False, attempt + 1,
                                         round(total_cpu, 3),
                                         round(time.monotonic() - t_start, 3),
                                         incidents, c.name)
            # policy bookkeeping for the next attempt
            if c.policy_params.get("cleanup_hook"):
                env_extra["AGENT_HEAL_DISK_FIXED"] = "1"
            # shrink is sticky: only a shrink_retry decision changes it, so a
            # later transient fault doesn't un-shrink an OOM recovery
            if decision.policy == "shrink_retry":
                shrink = decision.shrink_factor
            if decision.delay_s > 0:
                time.sleep(decision.delay_s)

        return SupervisionResult(False, self.max_attempts + 1,
                                 round(total_cpu, 3),
                                 round(time.monotonic() - t_start, 3),
                                 incidents, "attempts_exhausted")

    # -- internals ---------------------------------------------------
    def _watch(self, proc: subprocess.Popen, monitor: HeartbeatMonitor,
               stdout_f: Path, w0: float) -> str | None:
        """Returns 'hang' | 'timeout' | 'nan' | None (clean exit)."""
        last_size = 0
        while proc.poll() is None:
            if monitor.is_stale():
                self._kill_tree(proc)
                return "hang"
            if time.monotonic() - w0 > self.attempt_timeout_s:
                self._kill_tree(proc)
                return "timeout"
            # live NaN scan: tail new stdout for metric lines
            try:
                data = stdout_f.read_text(errors="replace")
            except OSError:
                data = ""
            new = data[last_size:]
            last_size = len(data)
            if scan_metrics(new.splitlines()):
                self._kill_tree(proc)
                return "nan"
            time.sleep(self.poll_s)
        return None

    @staticmethod
    def _kill_tree(proc: subprocess.Popen) -> None:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
