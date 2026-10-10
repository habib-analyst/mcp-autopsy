# mcp-autopsy 🔬

**Agent forensics as MCP tools.** Your coding agent died after 40 tool calls — ask Claude Code *why*, right in your editor.

`mcp-autopsy` wraps two shipped systems as Model Context Protocol tools:

- [agent-autopsy](https://github.com/habib-analyst/agent-autopsy) — post-mortem forensics: which step poisoned the trace, what the failure class is, whether it's safe to resume
- [agent-heal](https://github.com/habib-analyst/agent-heal) — self-healing supervision: run a job, classify each failure, apply the recovery policy automatically

Zero forensics MCP servers existed in an 8,600-server ecosystem. This is the first.

## 30-second demo

```bash
pip install mcp-autopsy
```

Add to your Claude Code config (`~/.claude.json` or `.mcp.json` in the project):

```json
{
  "mcpServers": {
    "mcp-autopsy": { "command": "mcp-autopsy" }
  }
}
```

Then, in Claude Code:

> *"My agent run died. Here's the trace:"* (paste the JSONL trace)
> → `diagnose_trace` returns the autopsy: **culprit step 5 — context poisoning (injected instruction in tool output at step 5). DO NOT RESUME, start fresh.**
> → `resume_verdict` returns the same as JSON for your resume logic.
> → `heal_job` runs your flaky training script under supervision — hangs killed, NaNs caught live, batch auto-shrunk on OOM.

## Tools

| Tool | Input | Output |
|---|---|---|
| `diagnose_trace(trace_jsonl)` | trace as JSONL text (generic / OpenAI / Anthropic formats auto-detected) | text autopsy report |
| `resume_verdict(trace_jsonl)` | same | JSON: `failure_found`, `culprit_step`, `primary_mode`, `evidence[]`, `resume{verdict, resume_from_step, reason}` |
| `heal_job(command, job_id, policy="auto", max_attempts=3, attempt_timeout_s=60)` | shell command + id | JSON: `recovered`, `attempts`, `incidents[]`, `final_failure` |

Resume verdicts: `SAFE_TO_RESUME` · `RESUME_WITH_FIX` (with exact step + correction) · `DO_NOT_RESUME` · `NO_FAILURE_FOUND`.

## Measured numbers

End-to-end through the real MCP wire protocol (stdio), 42 synthesized traces with known injected root causes (7 fault types × 6 fresh seeds, "debug" scenario):

| metric | value |
|---|---|
| attribution, exact culprit step | **1.000** |
| resume-verdict accuracy | **1.000** |
| clean-trace false-positive rate | **0.000** |
| `diagnose_trace` latency p50 / p95 | 0.020s / 0.034s |
| `resume_verdict` latency p50 / p95 | 0.018s / 0.033s |
| `heal_job` flaky script (fails attempt 0) | recovered in 2 attempts (0.62s) |
| `heal_job` always-failing script | correctly not recovered, escalated |

Full per-trace rows: `eval/results_mcp.json`. The corpus is synthesized (known ground truth by construction); the paper says so plainly.

## How it works

```
trace JSONL ──▶ diagnose_trace ──▶ detect_all() → mode scores
                                    counterfactual ablation per step
                                    → culprit step (max failure-score drop)
                                    → resume classifier
                                    → text report / JSON verdict

command ──────▶ heal_job ──▶ subprocess + heartbeat monitor + NaN scan
                                → classify(failure) → decide(policy)
                                → resume / shrink+resume / retry / escalate
                                → incident log per attempt
```

## Development

```bash
python -m venv .venv && .venv/bin/pip install -e . && .venv/bin/pip install pytest
.venv/bin/python -m pytest tests/ -q   # 7 tests incl. real stdio client roundtrip
.venv/bin/python eval/run_eval.py      # regenerates eval/results_mcp.json
```

## License

MIT. The vendored `agent_autopsy/` and `agent_heal/` packages are Habib Ur Rehman's own code, originally published at [habib-analyst/agent-autopsy](https://github.com/habib-analyst/agent-autopsy) and [habib-analyst/agent-heal](https://github.com/habib-analyst/agent-heal) under MIT.
