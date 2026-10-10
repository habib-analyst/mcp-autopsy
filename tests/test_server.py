"""Tests for mcp-autopsy: core logic + real MCP stdio client roundtrip."""
import asyncio
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mcp_autopsy.core import diagnose, verdict, heal  # noqa: E402

TRACE = open(os.path.join(os.path.dirname(__file__), "..", "examples",
                          "poisoned_run.jsonl")).read()

CLEAN_TRACE = "\n".join(json.dumps(e) for e in [
    {"step": 0, "kind": "tool_call", "tool": "search",
     "args": {"q": "mcp servers"}},
    {"step": 1, "kind": "tool_result", "tool": "search",
     "output": "3 results", "error": False},
    {"step": 2, "kind": "tool_call", "tool": "read", "args": {"f": "a.md"}},
    {"step": 3, "kind": "tool_result", "tool": "read",
     "output": "ok", "error": False},
])


def test_diagnose_poisoned():
    rep = diagnose(TRACE)
    assert isinstance(rep, str) and len(rep) > 100
    assert "culprit" in rep.lower() or "step" in rep.lower()


def test_verdict_poisoned():
    v = verdict(TRACE)
    assert v["failure_found"] is True
    assert isinstance(v["culprit_step"], int)
    assert v["resume"]["verdict"] in (
        "SAFE_TO_RESUME", "RESUME_WITH_FIX", "DO_NOT_RESUME",
        "NO_FAILURE_FOUND")


def test_verdict_clean():
    v = verdict(CLEAN_TRACE)
    assert v["failure_found"] is False
    assert v["resume"]["verdict"] == "NO_FAILURE_FOUND"


def test_heal_clean_job(tmp_path):
    out = heal(["/bin/true"], "t1", str(tmp_path / "r1"),
               max_attempts=2, attempt_timeout_s=10)
    assert out["recovered"] is True
    assert out["attempts"] == 1
    assert out["final_failure"] is None


def test_heal_failing_job_escalates(tmp_path):
    out = heal(["/bin/false"], "t2", str(tmp_path / "r2"),
               max_attempts=1, attempt_timeout_s=10, backoff_base=0.1)
    assert out["recovered"] is False
    assert out["attempts"] == 2  # attempt 0 + 1 retry
    assert len(out["incidents"]) == 2


def test_heal_recoverable_job(tmp_path):
    # fails on attempt 0 (env var), succeeds after
    script = tmp_path / "flaky.sh"
    script.write_text(
        "#!/bin/bash\n"
        "if [ \"$AGENT_HEAL_ATTEMPT\" = \"0\" ]; then exit 1; fi\n"
        "exit 0\n")
    script.chmod(0o755)
    out = heal([str(script)], "t3", str(tmp_path / "r3"),
               max_attempts=2, attempt_timeout_s=10, backoff_base=0.1)
    assert out["recovered"] is True
    assert out["attempts"] == 2


def test_mcp_stdio_roundtrip():
    """Real MCP client over the stdio transport (the actual wire protocol)."""
    async def go():
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        env = dict(os.environ)
        env["PYTHONPATH"] = (os.path.dirname(
            os.path.dirname(os.path.abspath(__file__))) +
            os.pathsep + env.get("PYTHONPATH", ""))
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "mcp_autopsy.server"],
            env=env, cwd=os.path.dirname(
                os.path.dirname(os.path.abspath(__file__))))
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = await session.list_tools()
                names = [t.name for t in tools.tools]
                assert {"diagnose_trace", "resume_verdict",
                        "heal_job"} <= set(names)

                r = await session.call_tool(
                    "resume_verdict", {"trace_jsonl": TRACE})
                v = json.loads(r.content[0].text)
                assert v["failure_found"] is True
                assert isinstance(v["culprit_step"], int)

                r2 = await session.call_tool(
                    "heal_job", {"command": ["/bin/true"],
                                 "job_id": "rt1", "max_attempts": 1,
                                 "attempt_timeout_s": 10})
                h = json.loads(r2.content[0].text)
                assert h["recovered"] is True
    asyncio.run(go())
