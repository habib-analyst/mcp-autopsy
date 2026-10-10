"""Core (non-MCP) implementations of the three tools.

These are thin adapters over the vendored agent_autopsy / agent_heal
packages — the intelligence lives there; this module handles I/O shape
(trace text in, JSON/text out) and the supervised-job loop.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import tempfile
import time
from pathlib import Path

from agent_autopsy.attribution import attribute, primary_mode
from agent_autopsy.ingest import ingest_jsonl
from agent_autopsy.report import text_report
from agent_autopsy.resume import resume_verdict as _resume_verdict

from agent_heal.classifier import classify, scan_metrics
from agent_heal.heartbeat import HeartbeatMonitor
from agent_heal.incidents import Incident, IncidentLog
from agent_heal.policies import decide, backoff


def _events_from_text(trace_jsonl: str):
    """Parse a trace given as JSONL text into normalized events."""
    with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as f:
        f.write(trace_jsonl)
        tmp = f.name
    try:
        return ingest_jsonl(tmp)
    finally:
        os.unlink(tmp)


def diagnose(trace_jsonl: str) -> str:
    """Full autopsy report for a failed agent trace (text)."""
    events = _events_from_text(trace_jsonl)
    result = attribute(events)
    verdict = _resume_verdict(result, events)
    return text_report(result, verdict, events)


def verdict(trace_jsonl: str) -> dict:
    """Machine-readable diagnosis: culprit step, failure class, resume verdict."""
    events = _events_from_text(trace_jsonl)
    result = attribute(events)
    v = _resume_verdict(result, events)
    evidence = [
        {"mode": e.mode, "step": e.step, "weight": round(e.weight, 3),
         "detail": e.detail}
        for e in (result.evidence or [])
    ]
    return {
        "failure_found": bool(result.failure_found),
        "culprit_step": result.culprit_step,
        "culprit_drop": round(result.culprit_drop, 3),
        "primary_mode": primary_mode(result),
        "mode_scores": {k: round(vv, 3)
                        for k, vv in (result.mode_scores or {}).items()},
        "evidence": evidence,
        "resume": v,
    }


def heal(command: list[str], job_id: str, run_dir: str | Path,
         policy: str = "auto", max_attempts: int = 3,
         attempt_timeout_s: float = 60.0, hang_timeout_s: float = 5.0,
         backoff_base: float = 0.5) -> dict:
    """Run a job under the self-healing supervisor.

    Each attempt: launch, heartbeat-watch, NaN-scan, classify the failure,
    select the recovery via decide(policy, ...), apply it. policy="auto"
    lets the classifier's taxonomy pick; otherwise force
    resume | retry_once | shrink_retry.
    """
    from agent_heal.taxonomy import FailureTaxonomy
    taxonomy = FailureTaxonomy(None)
    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    log = IncidentLog(str(run_dir / "incidents.jsonl"))
    progress = run_dir / "progress.jsonl"
    heartbeat = run_dir / "heartbeat"
    incidents: list[dict] = []
    t_start = time.monotonic()
    shrink = 1.0
    env_extra: dict = {}

    def _kill_tree(proc):
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass

    for attempt in range(max_attempts + 1):
        stdout_f = run_dir / f"stdout_attempt{attempt}.log"
        stderr_f = run_dir / f"stderr_attempt{attempt}.log"
        heartbeat.touch()
        env = dict(os.environ)
        env.update(env_extra)
        env["AGENT_HEAL_PROGRESS"] = str(progress)
        env["AGENT_HEAL_HEARTBEAT"] = str(heartbeat)
        env["AGENT_HEAL_ATTEMPT"] = str(attempt)
        env["AGENT_HEAL_SHRINK"] = str(shrink)

        w0 = time.monotonic()
        proc = subprocess.Popen(
            command, stdout=open(stdout_f, "w"), stderr=open(stderr_f, "w"),
            env=env, start_new_session=True)
        monitor = HeartbeatMonitor(heartbeat, hang_timeout_s)

        death = None
        last_size = 0
        while proc.poll() is None:
            if monitor.is_stale():
                _kill_tree(proc); death = "hang"; break
            if time.monotonic() - w0 > attempt_timeout_s:
                _kill_tree(proc); death = "timeout"; break
            try:
                data = stdout_f.read_text(errors="replace")
            except OSError:
                data = ""
            new = data[last_size:]
            last_size = len(data)
            if scan_metrics(new.splitlines()):
                _kill_tree(proc); death = "nan"; break
            time.sleep(0.1)
        rc = proc.wait()
        wall = time.monotonic() - w0
        stdout_text = stdout_f.read_text(errors="replace")
        stderr_text = stderr_f.read_text(errors="replace")
        metric_lines = [ln for ln in stdout_text.splitlines()
                        if "metric" in ln.lower()]
        hb_stale = death == "hang"

        if rc == 0 and not hb_stale and not scan_metrics(metric_lines):
            inc = Incident(job_id, attempt, "none", "none", rc, 0.0,
                           round(wall, 3), True)
            log.append(inc); incidents.append(_incident_dict(inc))
            return {"recovered": True, "attempts": attempt + 1,
                    "wall_s": round(time.monotonic() - t_start, 3),
                    "incidents": incidents, "final_failure": None}

        c = classify(exit_code=rc if death != "hang" else None,
                     stderr=stderr_text, metric_lines=metric_lines,
                     heartbeat_stale=hb_stale, timed_out=(death == "timeout"),
                     taxonomy=taxonomy)
        eff_policy = c.policy if policy == "auto" else policy
        params = c.policy_params if policy == "auto" else {}
        decision = decide(eff_policy, params, attempt, max_attempts,
                          backoff_base)
        inc = Incident(job_id, attempt, c.name, eff_policy, rc,
                       0.0, round(wall, 3), False, c.evidence)
        log.append(inc); incidents.append(_incident_dict(inc))

        if decision.escalate:
            return {"recovered": False, "attempts": attempt + 1,
                    "wall_s": round(time.monotonic() - t_start, 3),
                    "incidents": incidents, "final_failure": c.name}
        if decision.policy == "shrink_retry":
            shrink = decision.shrink_factor
        if decision.delay_s > 0:
            time.sleep(decision.delay_s)

    return {"recovered": False, "attempts": max_attempts + 1,
            "wall_s": round(time.monotonic() - t_start, 3),
            "incidents": incidents, "final_failure": "attempts_exhausted"}


def _incident_dict(inc: Incident) -> dict:
    return {"job_id": inc.job_id, "attempt": inc.attempt,
            "failure": inc.failure, "policy": inc.policy,
            "exit_code": inc.exit_code, "wall_s": inc.wall_s,
            "recovered": inc.recovered}
