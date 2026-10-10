"""mcp-autopsy MCP server: agent forensics as tools for Claude Code / Cursor.

Tools:
  diagnose_trace  — full autopsy report for a failed agent trace (text)
  resume_verdict  — machine-readable diagnosis + resume-safety verdict (JSON)
  heal_job        — run a command under the self-healing supervisor (JSON)

Stdio transport (default for Claude Code / Cursor):
  {"mcpServers": {"mcp-autopsy": {"command": "mcp-autopsy"}}}
after `pip install mcp-autopsy`.
"""
from __future__ import annotations

import tempfile
from typing import List

from mcp.server.fastmcp import FastMCP

from .core import diagnose as _diagnose
from .core import verdict as _verdict
from .core import heal as _heal

mcp = FastMCP("mcp-autopsy")


@mcp.tool()
def diagnose_trace(trace_jsonl: str) -> str:
    """Post-mortem forensics for a failed agent run.

    Paste the trace as JSONL (one event per line: tool_call / tool_result /
    message, or OpenAI/Anthropic tool-call logs — format is auto-detected).
    Returns a text autopsy report: whether a failure signature was found,
    the exact culprit step (counterfactual ablation), the failure class
    (tool misuse, context poisoning, plan drift, loop stall, premature
    termination, transient infra), and the resume-safety verdict.
    """
    return _diagnose(trace_jsonl)


@mcp.tool()
def resume_verdict(trace_jsonl: str) -> dict:
    """Machine-readable autopsy for a failed agent trace.

    Same input as diagnose_trace. Returns JSON with:
      failure_found, culprit_step, culprit_drop, primary_mode, mode_scores,
      evidence[], and resume{verdict, resume_from_step, reason, primary_mode}
    where verdict is one of SAFE_TO_RESUME | RESUME_WITH_FIX |
    DO_NOT_RESUME | NO_FAILURE_FOUND.
    """
    return _verdict(trace_jsonl)


@mcp.tool()
def heal_job(command: List[str], job_id: str, policy: str = "auto",
             max_attempts: int = 3, attempt_timeout_s: float = 60.0) -> dict:
    """Run a command under the self-healing supervisor.

    The supervisor launches the job, watches its heartbeat (hang detection),
    scans stdout for NaN metrics (divergence), classifies each failure and
    applies the recovery policy (resume / shrink batch + resume / retry /
    escalate), appending an incident record per attempt.

    policy="auto" lets the failure classifier pick the recovery from its
    taxonomy (default). Force "resume" | "retry_once" | "shrink_retry" to
    override. Returns JSON: recovered, attempts, wall_s, incidents[],
    final_failure.
    """
    run_dir = tempfile.mkdtemp(prefix=f"heal_{job_id}_")
    return _heal(command, job_id, run_dir, policy=policy,
                 max_attempts=max_attempts,
                 attempt_timeout_s=attempt_timeout_s)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
