"""Failure classifier: turn raw job-death evidence into a taxonomy entry.

Evidence channels (checked in priority order):
  1. heartbeat staleness  -> hang
  2. metric stream NaN/inf -> nan_divergence
  3. wall-time budget      -> timeout (before exit codes: our own timeout
                             kill delivers SIGKILL, which must not read as
                             infra_death)
  4. exit code / signal   -> infra_death (-9/-15), oom (137)
  5. stderr signatures    -> taxonomy regex match
  6. none matched         -> unknown:<last stderr line>
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

from .taxonomy import FailureTaxonomy, FailureEntry

_NAN_RE = re.compile(r"\b(nan|inf|-inf|infinity)\b", re.IGNORECASE)


@dataclass
class Classification:
    entry: FailureEntry | None
    name: str
    policy: str
    policy_params: dict
    evidence: str


def scan_metrics(lines: list[str]) -> str:
    """Return 'nan' if any metric line carries NaN/inf, else ''."""
    for ln in lines:
        if "metric" in ln.lower() and _NAN_RE.search(ln):
            return "nan"
    return ""


def classify(exit_code: int | None,
             stderr: str,
             metric_lines: list[str],
             heartbeat_stale: bool,
             timed_out: bool,
             taxonomy: FailureTaxonomy) -> Classification:
    metric_hit = scan_metrics(metric_lines)

    # 1. hang — heartbeat is the only signal for a wedged-but-alive process
    if heartbeat_stale:
        e = taxonomy.match(heartbeat_stale=True)
        return _pack(e, taxonomy, "heartbeat stale beyond timeout")

    # 2. NaN in the metric stream (even if the process later exited 0)
    if metric_hit:
        e = taxonomy.match(metric_hit=metric_hit)
        return _pack(e, taxonomy, f"metric stream contained {metric_hit}")

    # 3. wall-time budget — checked before exit codes because our own
    #    timeout kill delivers SIGKILL (-9), which would otherwise
    #    misclassify as infra_death
    if timed_out:
        for cand in taxonomy.entries:
            if cand.name == "timeout":
                return _pack(cand, taxonomy, "wall-time budget exceeded")

    # 4. signals / exit codes
    if exit_code is not None:
        e = taxonomy.match(exit_code=exit_code)
        if e:
            return _pack(e, taxonomy, f"exit code {exit_code}")

    # 5. stderr signatures
    if stderr:
        e = taxonomy.match(stderr=stderr)
        if e:
            return _pack(e, taxonomy, "stderr signature match")

    # 6. unknown — grow the taxonomy honestly
    lines = [ln for ln in (stderr or "").strip().splitlines() if ln.strip()]
    name = "unknown:" + (lines[-1][:80] if lines else f"exit={exit_code}")
    taxonomy.record(name, recovered=False)
    policy, params = taxonomy.policy_for(name)
    return Classification(entry=None, name=name, policy=policy,
                          policy_params=params,
                          evidence="no signature matched")


def _pack(entry: FailureEntry | None, taxonomy: FailureTaxonomy,
          evidence: str) -> Classification:
    if entry is None:
        return Classification(entry=None, name="unknown", policy="retry_once",
                              policy_params={}, evidence=evidence)
    policy, params = taxonomy.policy_for(entry.name)
    return Classification(entry=entry, name=entry.name, policy=policy,
                          policy_params=params, evidence=evidence)
