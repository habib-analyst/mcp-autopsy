"""Recovery policies with backoff.

Policies (selected by the classifier via the taxonomy):
  resume        — restart the job, skipping already-completed units
                  (via the checkpoint-resume protocol).
  shrink_retry  — halve the work unit (batch / load factor, floored), then
                  resume. For deterministic failures (OOM) a same-size retry
                  is provably futile; shrinking is the only recovery.
  retry_once    — one plain retry with backoff, then escalate.
  escalate      — stop; record the incident and surface it (alert).

Backoff: exponential with jitter, capped — keeps the supervisor from
hammering a sick system.
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass

POLICIES = ("resume", "shrink_retry", "retry_once", "escalate")


@dataclass
class PolicyDecision:
    policy: str
    params: dict
    attempt: int          # 0-based attempt that just failed
    delay_s: float        # backoff before the next attempt
    escalate: bool = False
    shrink_factor: float = 1.0  # <1 means "halve the work unit"


def backoff(attempt: int, base: float = 2.0, cap: float = 60.0,
            jitter: float = 0.25) -> float:
    d = min(cap, base * (2 ** attempt))
    return d * (1.0 + random.uniform(-jitter, jitter))


def decide(policy: str, params: dict, attempt: int,
           max_attempts: int = 5,
           backoff_base: float = 2.0) -> PolicyDecision:
    """Map (policy, attempt) -> concrete decision.

    attempt counts failures so far. Returns escalate=True when the policy's
    budget is exhausted.
    """
    if policy == "resume":
        if attempt >= max_attempts:
            return PolicyDecision(policy, params, attempt,
                                  delay_s=0.0, escalate=True)
        return PolicyDecision(policy, params, attempt,
                              delay_s=backoff(attempt, backoff_base))
    if policy == "shrink_retry":
        factor = float(params.get("shrink_factor", 0.5))
        # shrink for the NEXT attempt: compounds per failure (0.5, 0.25, ...)
        next_shrink = factor ** (attempt + 1)
        if attempt >= max_attempts:
            return PolicyDecision(policy, params, attempt, delay_s=0.0,
                                  escalate=True, shrink_factor=next_shrink)
        return PolicyDecision(policy, params, attempt,
                              delay_s=backoff(attempt, backoff_base),
                              shrink_factor=next_shrink)
    if policy == "retry_once":
        allowed = int(params.get("max_retries", 1))
        if attempt >= allowed:
            return PolicyDecision(policy, params, attempt, delay_s=0.0,
                                  escalate=True)
        return PolicyDecision(policy, params, attempt,
                              delay_s=backoff(attempt, backoff_base))
    # escalate and anything unknown
    return PolicyDecision(policy, params, attempt, delay_s=0.0, escalate=True)
