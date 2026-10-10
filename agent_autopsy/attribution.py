"""Causal attribution by counterfactual ablation.

Core idea: a step "caused" the failure if removing it from the trace makes
the failure signature disappear. For every candidate step k we recompute the
total failure score on the trace with step k deleted; the step whose removal
produces the largest score drop is the attributed culprit.

Onset correction: for sustained failures (loops, drift runs) single-step
ablation spreads credit across the whole pattern. When the strongest
evidence item names an explicit step range "(steps A..B)" and the top
ablation step falls inside it, we attribute to the range start — the step
where the poisoned pattern began. This is the forensically correct answer
to "which call poisoned the trajectory".

This is a genuine counterfactual method (ablation-based causal inference
over detector outputs) and runs fully offline — no model calls.
"""
import re
from dataclasses import dataclass, field

from .taxonomy import MODES, detect_all

RANGE_RE = re.compile(r"\(steps (\d+)\.\.(\d+)\)")


@dataclass
class StepAttribution:
    step: int
    score_drop: float
    top_modes: list  # [(mode, weight_removed)]


@dataclass
class AutopsyResult:
    n_steps: int
    evidence: list = field(default_factory=list)
    mode_scores: dict = field(default_factory=dict)
    culprit_step: int | None = None
    culprit_drop: float = 0.0
    per_step: list = field(default_factory=list)  # [StepAttribution]
    failure_found: bool = False


def failure_score(events):
    return sum(e.weight for e in detect_all(events))


def mode_scores(events):
    scores = {m: 0.0 for m in MODES}
    for ev in detect_all(events):
        scores[ev.mode] = scores.get(ev.mode, 0.0) + ev.weight
    return scores


def attribute(events, min_drop=0.5):
    """Run the full autopsy. Returns AutopsyResult."""
    events = sorted(events, key=lambda e: e["step"])
    result = AutopsyResult(n_steps=len(events))
    result.evidence = detect_all(events)
    result.mode_scores = mode_scores(events)
    base = sum(result.mode_scores.values())
    if base <= 0:
        return result  # no failure signature at all

    candidates = [e["step"] for e in events
                  if e["kind"] in ("tool_result", "tool_call", "message")
                  and e["role"] != "user"]
    attributions = []
    for k in candidates:
        ablated = [e for e in events if e["step"] != k]
        ev_abl = detect_all(ablated)
        removed = base - sum(e.weight for e in ev_abl)
        # which modes lost weight?
        by_mode = {}
        for ev in result.evidence:
            by_mode.setdefault(ev.mode, 0.0)
        for ev in ev_abl:
            by_mode[ev.mode] = by_mode.get(ev.mode, 0.0) - ev.weight
        top = sorted(((m, w) for m, w in by_mode.items() if w > 1e-9),
                     key=lambda x: -x[1])[:3]
        attributions.append(StepAttribution(k, removed, top))

    attributions.sort(key=lambda a: -a.score_drop)
    best = attributions[0] if attributions else None
    if best and best.score_drop >= min_drop:
        culprit, drop = best.step, best.score_drop
        # onset correction: snap to the start of a ranged failure pattern
        top_ev = max(result.evidence, key=lambda e: e.weight, default=None)
        if top_ev is not None:
            m = RANGE_RE.search(top_ev.detail)
            if m:
                a, b = int(m.group(1)), int(m.group(2))
                if a <= best.step <= b:
                    culprit = a
        # the reported culprit leads the ranking
        attributions.sort(key=lambda x: (x.step != culprit, -x.score_drop))
        result.per_step = attributions
        result.culprit_step = culprit
        result.culprit_drop = drop
        result.failure_found = True
    else:
        result.per_step = attributions
    return result


def primary_mode(result):
    if not result.mode_scores:
        return None
    m = max(result.mode_scores.items(), key=lambda x: x[1])
    return m[0] if m[1] > 0 else None
