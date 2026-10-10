"""Failure-mode taxonomy: offline detectors that scan an agent trace and
emit weighted evidence for each failure mode.

Modes:
  tool_misuse          bad arguments, unknown tools, error->identical-retry
  context_poisoning    injected instructions in tool output, topic hijack
  plan_drift           sustained off-plan actions
  loop_stall           repeated identical calls / ping-pong with no progress
  premature_termination "done" claim without evidence, or quit right after error
  transient_infra      timeouts / rate limits / connection failures
"""
from dataclasses import dataclass, field

from .features import (
    COMPLETION_RE, INJECTION_RE, cosine, has_error, is_transient,
    tf_vector, tokens,
)

MODES = {
    "tool_misuse": "bad arguments, schema violations, error->identical-retry",
    "context_poisoning": "injected instructions or topic hijack via tool output",
    "plan_drift": "sustained actions unrelated to the stated plan",
    "loop_stall": "repeated identical calls / ping-pong with no progress",
    "premature_termination": "'done' claim without evidence, or quit after error",
    "transient_infra": "timeouts, rate limits, connection failures",
}


@dataclass
class Evidence:
    mode: str
    step: int
    weight: float
    detail: str = ""


def _sig(e):
    """Identity signature of a tool call for repetition detection."""
    if e["kind"] != "tool_call":
        return None
    args = e.get("args")
    if isinstance(args, dict):
        argkey = tuple(sorted((k, str(v)[:60]) for k, v in args.items()))
    else:
        argkey = (str(args)[:60],)
    return (e.get("tool"), argkey)


def detect_tool_misuse(events):
    out = []
    sigs = [_sig(e) for e in events]
    # repetition: same signature 3+ times in the trace
    seen = {}
    for e, s in zip(events, sigs):
        if s is None:
            continue
        seen.setdefault(s, []).append(e["step"])
    for s, steps in seen.items():
        if len(steps) >= 3:
            out.append(Evidence("tool_misuse", steps[2],
                                1.0 + 0.5 * (len(steps) - 3),
                                f"identical {s[0]} call x{len(steps)} "
                                f"(steps {steps[0]}..{steps[-1]})"))
    # error -> identical retry of the immediately preceding call
    for i, e in enumerate(events):
        if e["kind"] == "tool_result" and e.get("error"):
            nxt = events[i + 1] if i + 1 < len(events) else None
            prev = events[i - 1] if i - 1 >= 0 else None
            if (nxt and prev and nxt["kind"] == "tool_call"
                    and prev["kind"] == "tool_call"
                    and _sig(nxt) == _sig(prev) and _sig(prev) is not None):
                out.append(Evidence("tool_misuse", nxt["step"], 1.5,
                                    f"retried {nxt['tool']} unchanged after error"))
    # malformed args captured as _raw (unparseable JSON)
    for e in events:
        if e["kind"] == "tool_call" and isinstance(e.get("args"), dict) \
                and "_raw" in e["args"]:
            out.append(Evidence("tool_misuse", e["step"], 1.2,
                                f"{e['tool']} called with unparseable arguments"))
    return out


def detect_context_poisoning(events):
    out = []
    plan_vec = _plan_vector(events)
    baseline = None
    for i, e in enumerate(events):
        if e["kind"] != "tool_result":
            continue
        text = e.get("text", "")
        if INJECTION_RE.search(text or ""):
            out.append(Evidence("context_poisoning", e["step"], 3.0,
                                "tool output contains instruction-override pattern"))
        # topic hijack: plan-similarity collapses right after this tool result
        post = events[i + 1:i + 5]
        post_vec = tf_vector(" ".join(p.get("text", "") for p in post))
        sim = cosine(plan_vec, post_vec)
        if baseline is None and i < 6:
            baseline = sim
        if baseline is not None and baseline > 0.15 and sim < 0.12 \
                and (baseline - sim) > 0.25:
            out.append(Evidence(
                "context_poisoning", e["step"],
                2.0 * (baseline - sim),
                f"topic similarity to plan collapsed {baseline:.2f}->{sim:.2f} "
                f"after this tool result"))
    return out


def _plan_vector(events):
    for e in events:
        if e["role"] == "assistant" and e["kind"] == "message" and e.get("text"):
            return tf_vector(e["text"])
    return tf_vector("")


def _plan_keywords(events, top_n=25):
    vec = _plan_vector(events)
    return set(w for w, _ in vec.most_common(top_n))


def _blob(e):
    parts = [(e.get("tool") or "").replace("_", " ").replace("-", " "),
             e.get("text") or ""]
    args = e.get("args")
    if isinstance(args, dict):
        parts.extend(str(v) for v in args.values())
    return " ".join(parts)


def _median(xs):
    xs = sorted(xs)
    n = len(xs)
    if not n:
        return 0.0
    return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2


def detect_plan_drift(events):
    out = []
    plan_kw = _plan_keywords(events)
    if not plan_kw:
        return out
    seq = [e for e in events
           if e["step"] > 2 and e["kind"] in ("tool_call", "tool_result",
                                             "message")]
    # sliding windows of 4 consecutive actions (robust to alignment)
    scored = []
    for i in range(len(seq) - 3):
        window = seq[i:i + 4]
        # drift is about the agent's *choices*: error results are system
        # fallout, not choices, so they don't count toward (or against) drift
        choices = [w for w in window
                   if not (w["kind"] == "tool_result" and w.get("error"))]
        ctokens = []
        for w in choices:
            ctokens.extend(tokens(_blob(w)))
        if len(ctokens) < 6:
            continue  # vacuous window (e.g. empty-arg calls): not drift
        union = set(ctokens)
        overlap = len(union & plan_kw) / max(1, len(plan_kw))
        scored.append((overlap, window))
    if not scored:
        return out
    # adaptive bar: a window is drift only if it is far below BOTH the
    # absolute bar and this trace's own typical plan-grounding. (A fixed
    # bar alone misfires on synonym-heavy but on-plan windows.)
    med = _median([o for o, _ in scored])
    thresh = min(0.08, med - 0.05) if med >= 0.08 else 0.08
    for overlap, window in scored:
        if overlap < thresh:
            a, b = window[0]["step"], window[-1]["step"]
            out.append(Evidence("plan_drift", a, 1.5,
                                f"4 consecutive actions share <8% of plan "
                                f"keywords (overlap {overlap:.2f}) "
                                f"(steps {a}..{b})"))
            for w in window:
                out.append(Evidence("plan_drift", w["step"], 0.4,
                                    "step inside off-plan window"))
    return out


def detect_loop_stall(events):
    out = []
    sigs = [_sig(e) for e in events]
    # run of >=4 identical consecutive tool calls
    run, run_sig = 0, None
    for e, s in zip(events, sigs):
        if s is not None and s == run_sig:
            run += 1
        else:
            if run >= 4:
                out.append(Evidence("loop_stall", e["step"] - 1, 2.0,
                                    f"{run}x consecutive identical "
                                    f"{run_sig[0]} calls, no progress"))
            run, run_sig = (1, s) if s is not None else (0, None)
    if run >= 4:
        out.append(Evidence("loop_stall", events[-1]["step"], 2.0,
                            f"{run}x consecutive identical {run_sig[0]} calls"))
    # call-error cycles: same failing call retried >=3 times with error
    # results interleaved (the classic stall pattern)
    i = 0
    while i < len(events):
        e = events[i]
        s = _sig(e)
        if s is None:
            i += 1
            continue
        cycles, j = 0, i
        while j + 1 < len(events) and _sig(events[j]) == s \
                and events[j + 1]["kind"] == "tool_result" \
                and events[j + 1].get("error"):
            cycles += 1
            j += 2
        if cycles >= 3:
            out.append(Evidence(
                "loop_stall", e["step"], 2.0,
                f"{cycles}x call-error cycles of {s[0]} "
                f"(steps {e['step']}..{events[j - 1]['step']})"))
            i = j
        else:
            i += 1
    return out


def detect_premature_termination(events):
    out = []
    if not events:
        return out
    last = events[-1]
    if last["role"] == "assistant" and last["kind"] == "message" \
            and COMPLETION_RE.search(last.get("text", "")):
        # evidence of real completion? a clean tool result near the end
        recent = events[max(0, len(events) - 6):-1]
        clean_results = [e for e in recent
                         if e["kind"] == "tool_result" and not e.get("error")]
        last_err = any(e["kind"] == "tool_result" and e.get("error")
                       for e in recent)
        if last_err and not clean_results:
            out.append(Evidence("premature_termination", last["step"], 2.5,
                                "'done' claimed right after unresolved errors"))
        elif not clean_results and len(events) < 10:
            out.append(Evidence("premature_termination", last["step"], 2.0,
                                "'done' claimed with no successful tool "
                                "results in trace"))
    return out


def detect_transient_infra(events):
    out = []
    for e in events:
        if e["kind"] == "tool_result" and e.get("error") \
                and is_transient(e.get("text", "")):
            out.append(Evidence("transient_infra", e["step"], 1.0,
                                "transient infrastructure error "
                                "(timeout/rate-limit/connection)"))
    return out


DETECTORS = [detect_tool_misuse, detect_context_poisoning, detect_plan_drift,
             detect_loop_stall, detect_premature_termination,
             detect_transient_infra]


def detect_all(events):
    """Run every detector; return flat list of Evidence."""
    out = []
    for det in DETECTORS:
        try:
            out.extend(det(events))
        except Exception:
            continue
    return out
