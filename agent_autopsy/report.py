"""Autopsy reports: plain-text (CLI) and self-contained HTML."""
import html as _html

from .attribution import primary_mode
from .taxonomy import MODES


def _step_label(events, step):
    for e in events:
        if e["step"] == step:
            tool = f" [{e['tool']}]" if e.get("tool") else ""
            txt = (e.get("text") or "")[:90].replace("\n", " ")
            return f"step {step} ({e['role']}/{e['kind']}{tool}) {txt}"
    return f"step {step}"


def text_report(result, verdict, events):
    L = []
    L.append("=" * 64)
    L.append("AGENT AUTOPSY REPORT")
    L.append("=" * 64)
    L.append(f"Trace steps analyzed : {result.n_steps}")
    L.append(f"Failure detected     : {'YES' if result.failure_found else 'NO'}")
    if not result.failure_found:
        L.append("No failure signature found. Nothing to attribute.")
        return "\n".join(L)
    pm = primary_mode(result)
    L.append(f"Primary failure mode : {pm} — {MODES.get(pm, '')}")
    L.append("")
    L.append("--- CULPRIT STEP (counterfactual ablation) ---")
    L.append(f"  {_step_label(events, result.culprit_step)}")
    L.append(f"  removing this step reduces the failure score by "
             f"{result.culprit_drop:.2f}")
    L.append("")
    L.append("--- FAILURE-MODE SCORES ---")
    for m, s in sorted(result.mode_scores.items(), key=lambda x: -x[1]):
        if s > 0:
            L.append(f"  {m:22s} {s:6.2f}")
    L.append("")
    L.append("--- EVIDENCE ---")
    for ev in sorted(result.evidence, key=lambda e: -e.weight)[:12]:
        L.append(f"  [step {ev.step:>3}] {ev.mode:22s} "
                 f"w={ev.weight:5.2f}  {ev.detail[:100]}")
    L.append("")
    L.append("--- RESUME VERDICT ---")
    L.append(f"  verdict          : {verdict['verdict']}")
    L.append(f"  resume_from_step : {verdict['resume_from_step']}")
    L.append(f"  reason           : {verdict['reason']}")
    L.append("=" * 64)
    return "\n".join(L)


def html_report(result, verdict, events):
    esc = _html.escape
    rows = []
    for ev in sorted(result.evidence, key=lambda e: -e.weight):
        hot = " class='hot'" if ev.step == result.culprit_step else ""
        rows.append(
            f"<tr{hot}><td>{ev.step}</td><td>{esc(ev.mode)}</td>"
            f"<td>{ev.weight:.2f}</td><td>{esc(ev.detail[:160])}</td></tr>")
    scores = "".join(
        f"<tr><td>{esc(m)}</td><td>{s:.2f}</td></tr>"
        for m, s in sorted(result.mode_scores.items(), key=lambda x: -x[1])
        if s > 0)
    culprit = (f"<p><b>Culprit step {result.culprit_step}</b> "
                f"(ablation drops failure score by {result.culprit_drop:.2f})<br>"
                f"<code>{esc(_step_label(events, result.culprit_step))}</code></p>"
                if result.failure_found else "<p>No failure signature found.</p>")
    pm = primary_mode(result) or "—"
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8">
<title>Agent Autopsy Report</title>
<style>
body{{font-family:system-ui,sans-serif;max-width:900px;margin:2em auto;padding:0 1em;color:#1a1a1a}}
table{{border-collapse:collapse;width:100%}}th,td{{border:1px solid #ccc;padding:6px 10px;text-align:left}}
th{{background:#f4f4f4}}tr.hot td{{background:#fff3cd;font-weight:bold}}
.verdict{{padding:1em;border-left:4px solid #d97706;background:#fffbeb}}
code{{background:#f4f4f4;padding:2px 6px;border-radius:4px}}
</style></head><body>
<h1>&#128128; Agent Autopsy Report</h1>
<p>Steps analyzed: <b>{result.n_steps}</b> &middot; Failure detected: \
<b>{"YES" if result.failure_found else "NO"}</b> &middot; \
Primary mode: <b>{esc(pm)}</b></p>
{culprit}
<h2>Failure-mode scores</h2>
<table><tr><th>mode</th><th>score</th></tr>{scores}</table>
<h2>Evidence</h2>
<table><tr><th>step</th><th>mode</th><th>weight</th><th>detail</th></tr>
{"".join(rows)}</table>
<h2>Resume verdict</h2>
<div class="verdict"><b>{esc(verdict['verdict'])}</b><br>
resume from step: <b>{verdict['resume_from_step']}</b><br>
{esc(verdict['reason'])}</div>
<p><small>Generated offline by agent-autopsy (ablation-based causal attribution). \
No model calls were made.</small></p>
</body></html>"""
