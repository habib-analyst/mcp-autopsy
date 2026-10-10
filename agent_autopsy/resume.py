"""Resume-safety classifier: given the autopsy, decide whether the failed
run may be resumed, from where, and with what precaution.

Verdicts:
  SAFE_TO_RESUME      transient cause; continue from last completed step
  RESUME_WITH_FIX     cause identified and fixable; resume from a given step
                      with a stated correction (redact output / fix args /
                      replan)
  DO_NOT_RESUME       context compromised or unrecoverable; start fresh
  NO_FAILURE_FOUND    nothing to resume from
"""
from .attribution import primary_mode

SAFE = "SAFE_TO_RESUME"
WITH_FIX = "RESUME_WITH_FIX"
NO_RESUME = "DO_NOT_RESUME"
NO_FAILURE = "NO_FAILURE_FOUND"


def _last_completed_step(events):
    """Last step that looks like forward progress: a non-error tool result
    or a tool call."""
    last = None
    for e in events:
        if (e["kind"] == "tool_result" and not e.get("error")) \
                or e["kind"] == "tool_call":
            last = e["step"]
    return last


def _last_onplan_step(events, culprit):
    """Latest step before the culprit that is not itself evidence-heavy."""
    if culprit is None:
        return _last_completed_step(events)
    cands = [e["step"] for e in events
             if e["step"] < culprit and e["kind"] in ("tool_call", "tool_result")]
    return max(cands) if cands else None


def resume_verdict(result, events):
    """Returns dict(verdict, resume_from_step, reason, primary_mode)."""
    if not result.failure_found:
        return {"verdict": NO_FAILURE, "resume_from_step": None,
                "reason": "no failure signature detected in trace",
                "primary_mode": None}
    mode = primary_mode(result)
    culprit = result.culprit_step

    if mode == "context_poisoning":
        return {"verdict": NO_RESUME, "resume_from_step": None,
                "reason": "tool output poisoned the context; resuming would "
                          "replay the poison. Restart with clean context and "
                          "sanitize untrusted tool outputs.",
                "primary_mode": mode}
    if mode == "transient_infra":
        return {"verdict": SAFE, "resume_from_step": _last_completed_step(events),
                "reason": "failure was transient infrastructure noise; "
                          "safe to retry from last completed step.",
                "primary_mode": mode}
    if mode == "tool_misuse":
        return {"verdict": WITH_FIX, "resume_from_step": culprit,
                "reason": "tool misuse at the culprit step; resume from that "
                          "step with corrected arguments (see detail).",
                "primary_mode": mode}
    if mode == "loop_stall":
        return {"verdict": WITH_FIX,
                "resume_from_step": _last_onplan_step(events, culprit),
                "reason": "agent stalled in a call loop; resume from the last "
                          "productive step with an explicit anti-loop "
                          "instruction.",
                "primary_mode": mode}
    if mode == "plan_drift":
        return {"verdict": WITH_FIX,
                "resume_from_step": _last_onplan_step(events, culprit),
                "reason": "agent drifted off-plan; resume from the last "
                          "on-plan step with the plan restated.",
                "primary_mode": mode}
    if mode == "premature_termination":
        return {"verdict": SAFE, "resume_from_step": _last_completed_step(events),
                "reason": "run stopped early without completing; safe to "
                          "continue from last completed step.",
                "primary_mode": mode}
    return {"verdict": WITH_FIX, "resume_from_step": culprit,
            "reason": f"primary failure mode '{mode}'; manual review advised.",
            "primary_mode": mode}
