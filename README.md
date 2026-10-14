[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE) [![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)] [![GitHub stars](https://img.shields.io/github/stars/habib-analyst/mcp-autopsy.svg)] [![M8ven Score](https://m8ven.ai/badge/mcp/habib-analyst/mcp-autopsy)](https://m8ven.ai/mcp/habib-analyst/mcp-autopsy?s=readme)

# mcp-autopsy

Agent forensics as MCP servers: diagnose dead traces and get resume verdicts inside Claude Code.

## Quickstart

```bash
git clone https://github.com/habib-analyst/mcp-autopsy.git && cd mcp-autopsy
pip install -e .          # stdio MCP server; entry point: mcp-autopsy
```

Add to your Claude Code MCP config (`.mcp.json`):

```json
{ "mcpServers": { "mcp-autopsy": { "command": "mcp-autopsy" } } }
```

Three tools become available: `diagnose_trace(trace_jsonl)`, `resume_verdict(trace_jsonl)`, `heal_job(command, job_id, policy="auto", max_attempts=3, attempt_timeout_s=60)`.

Example — paste a dead run's trace and ask why it died:

> *"My agent run died. Here's the trace:"*
> → `diagnose_trace` returns the autopsy: **culprit step 5 — context poisoning (injected instruction in tool output at step 5). DO NOT RESUME, start fresh.**
> → `resume_verdict` returns the same verdict as JSON for your resume logic (`failure_found`, `culprit_step`, `primary_mode`, `evidence[]`, `resume{verdict, resume_from_step, reason}`).
> → `heal_job(command=["python3", "train.py"], job_id="exp-1")` runs your flaky script under supervision — hangs killed, batch auto-shrunk on OOM.

Resume verdicts: `SAFE_TO_RESUME` · `RESUME_WITH_FIX` (exact step + correction) · `DO_NOT_RESUME` · `NO_FAILURE_FOUND`.

## Measured results

End-to-end through the real MCP stdio wire protocol — 42 synthesized traces with known injected root causes (7 fault types × 6 fresh seeds):

| metric | value |
|---|---|
| attribution, exact culprit step | **1.000** (42/42) |
| resume-verdict accuracy | **1.000** (42/42) |
| clean-trace false-positive rate | 0.000 |
| `diagnose_trace` latency p50 / p95 | 0.020 s / 0.034 s |
| `resume_verdict` latency p50 / p95 | 0.018 s / 0.033 s |
| `heal_job`, flaky script (fails attempt 0) | recovered in 2 attempts (0.62 s) |
| `heal_job`, always-failing script | not recovered — correctly escalated |
| tests | 7/7 (incl. real stdio client roundtrip) |

Per-trace rows: `eval/results_mcp.json`. The corpus is synthesized — ground truth known by construction; a real-world fault mix will be messier.

## How it works

```
trace JSONL ──▶ diagnose_trace ──▶ detectors (6 failure modes) ─▶ counterfactual ablation per step
                                                             → culprit step (max failure-score drop)
                                                             → resume classifier
                                                             → text report / JSON verdict

command ─────▶ heal_job ──▶ subprocess + heartbeat monitor + NaN scan → classify failure
                                                              → resume / shrink+resume / retry / escalate
                                                              → incident log per attempt
```

This wraps two shipped systems as MCP tools: [agent-autopsy](https://github.com/habib-analyst/agent-autopsy) (post-mortem forensics) and [agent-heal](https://github.com/habib-analyst/agent-heal) (self-healing supervision).

---
Built by [Habib Ur Rehman](https://github.com/habib-analyst) · ML/AI Engineer & healthcare-AI researcher ·
[LinkedIn](https://www.linkedin.com/in/hur-dev) · [habib.top](https://habib.top)
