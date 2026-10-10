"""mcp-autopsy evaluation: real MCP client over stdio.

- Corpus: 42 synthesized traces with KNOWN injected root causes
  (7 fault types x 6 seeds, "debug" scenario, seed0=9000 — fresh seeds,
  never touched during agent-autopsy development).
- Each trace is sent as JSONL text through the actual MCP wire protocol
  (stdio) to the `diagnose_trace` and `resume_verdict` tools.
- Metrics: attribution exact-step accuracy, resume-verdict accuracy,
  clean-trace false-positive rate, per-tool call latency (p50/p95).
- Plus: `heal_job` on 2 fault-injected jobs (flaky-then-clean,
  always-fails) verifying recovery behavior through the MCP tool.

Writes eval/results_mcp.json. CPU-only, $0.
"""
import asyncio
import json
import os
import statistics
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "eval"))

from corpus import build_corpus  # noqa: E402 — vendored from agent-autopsy (MIT)

SEEDS_PER_TYPE = 6
SEED0 = 9000
SCENARIO = "debug"


def to_jsonl(events):
    return "\n".join(json.dumps(e) for e in events)


async def main():
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    corpus = build_corpus(SEEDS_PER_TYPE, SEED0, SCENARIO)
    print(f"corpus: {len(corpus)} traces")

    env = dict(os.environ)
    env["PYTHONPATH"] = ROOT + os.pathsep + env.get("PYTHONPATH", "")
    params = StdioServerParameters(
        command=sys.executable, args=["-m", "mcp_autopsy.server"],
        env=env, cwd=ROOT)

    rows = []
    lat_diag, lat_verd = [], []
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            for t in corpus:
                text = to_jsonl(t["events"])
                t0 = time.monotonic()
                r = await session.call_tool(
                    "resume_verdict", {"trace_jsonl": text})
                lat_verd.append(time.monotonic() - t0)
                v = json.loads(r.content[0].text)

                t0 = time.monotonic()
                r2 = await session.call_tool(
                    "diagnose_trace", {"trace_jsonl": text})
                lat_diag.append(time.monotonic() - t0)
                rep = r2.content[0].text

                rows.append({
                    "fault": t["fault"], "seed": t["seed"],
                    "true_culprit": t["true_culprit"],
                    "pred_culprit": v["culprit_step"],
                    "exact_hit": (v["culprit_step"] == t["true_culprit"]),
                    "expected_verdict": t["expected_verdict"],
                    "pred_verdict": v["resume"]["verdict"],
                    "report_chars": len(rep),
                })

            # heal_job: flaky script (fails attempt 0, then clean)
            import tempfile
            d = tempfile.mkdtemp(prefix="mcp_eval_")
            flaky = os.path.join(d, "flaky.sh")
            with open(flaky, "w") as f:
                f.write("#!/bin/bash\n"
                        "if [ \"$AGENT_HEAL_ATTEMPT\" = \"0\" ]; then "
                        "echo metric=nan; exit 1; fi\nexit 0\n")
            os.chmod(flaky, 0o755)
            t0 = time.monotonic()
            rh = await session.call_tool(
                "heal_job", {"command": [flaky], "job_id": "eval-flaky",
                             "max_attempts": 2, "attempt_timeout_s": 15})
            lat_heal = time.monotonic() - t0
            heal_flaky = json.loads(rh.content[0].text)

            rd = await session.call_tool(
                "heal_job", {"command": ["/bin/false"],
                             "job_id": "eval-dead", "max_attempts": 1,
                             "attempt_timeout_s": 15})
            heal_dead = json.loads(rd.content[0].text)

    n = len(rows)
    exact = sum(r["exact_hit"] for r in rows)
    vacc = sum(r["pred_verdict"] == r["expected_verdict"] for r in rows)
    clean = [r for r in rows if r["fault"] == "clean"]
    clean_fp = sum(r["pred_verdict"] != "NO_FAILURE_FOUND" for r in clean)
    by_fault = {}
    for r in rows:
        d = by_fault.setdefault(r["fault"], {"n": 0, "exact": 0, "vacc": 0})
        d["n"] += 1
        d["exact"] += r["exact_hit"]
        d["vacc"] += r["pred_verdict"] == r["expected_verdict"]

    def pct(q, xs):
        s = sorted(xs)
        return round(s[min(len(s) - 1, int(q * len(s)))], 3)

    summary = {
        "n_traces": n, "scenario": SCENARIO, "seed0": SEED0,
        "attribution_exact_step": round(exact / n, 4),
        "resume_verdict_acc": round(vacc / n, 4),
        "clean_false_positive_rate": round(clean_fp / max(1, len(clean)), 4),
        "by_fault": {k: {"n": v["n"],
                         "exact": round(v["exact"] / v["n"], 4),
                         "vacc": round(v["vacc"] / v["n"], 4)}
                     for k, v in sorted(by_fault.items())},
        "latency_s": {
            "diagnose_trace": {"p50": pct(0.5, lat_diag),
                               "p95": pct(0.95, lat_diag)},
            "resume_verdict": {"p50": pct(0.5, lat_verd),
                               "p95": pct(0.95, lat_verd)},
            "heal_job_flaky": round(lat_heal, 3),
        },
        "heal_job": {
            "flaky_recovered": heal_flaky["recovered"],
            "flaky_attempts": heal_flaky["attempts"],
            "dead_recovered": heal_dead["recovered"],
            "dead_attempts": heal_dead["attempts"],
            "dead_final_failure": heal_dead["final_failure"],
        },
    }
    with open(os.path.join(ROOT, "eval", "results_mcp.json"), "w") as f:
        json.dump({"summary": summary, "rows": rows}, f, indent=2)
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
