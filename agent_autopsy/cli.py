"""CLI: agent-autopsy trace.jsonl [--format auto|jsonl|openai|anthropic]
[--html report.html] [--json out.json]"""
import argparse
import json
import sys

from .attribution import attribute
from .ingest import ingest, ingest_anthropic, ingest_jsonl, ingest_openai
from .report import html_report, text_report
from .resume import resume_verdict


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="agent-autopsy",
        description="Post-mortem forensics for failed agent runs: "
                    "find the step that poisoned the trajectory and get a "
                    "resume-safety verdict. Fully offline.")
    ap.add_argument("trace", help="trace file (JSONL / OpenAI / Anthropic)")
    ap.add_argument("--format", default="auto",
                    choices=["auto", "jsonl", "openai", "anthropic"])
    ap.add_argument("--html", default=None, help="write HTML report here")
    ap.add_argument("--json", default=None, help="write machine-readable "
                    "result JSON here")
    ap.add_argument("--quiet", action="store_true",
                    help="only print the verdict line")
    args = ap.parse_args(argv)

    if args.format == "jsonl":
        events = ingest_jsonl(args.trace)
    elif args.format == "openai":
        with open(args.trace) as f:
            events = ingest_openai(json.load(f))
    elif args.format == "anthropic":
        with open(args.trace) as f:
            events = ingest_anthropic(json.load(f))
    else:
        events = ingest(args.trace)

    if not events:
        print("agent-autopsy: no events parsed from trace", file=sys.stderr)
        return 2

    result = attribute(events)
    verdict = resume_verdict(result, events)

    if args.quiet:
        print(f"{verdict['verdict']} culprit={result.culprit_step} "
              f"mode={verdict['primary_mode']}")
    else:
        print(text_report(result, verdict, events))

    if args.html:
        with open(args.html, "w") as f:
            f.write(html_report(result, verdict, events))
        print(f"\nHTML report written to {args.html}", file=sys.stderr)

    if args.json:
        payload = {
            "n_steps": result.n_steps,
            "failure_found": result.failure_found,
            "culprit_step": result.culprit_step,
            "culprit_drop": result.culprit_drop,
            "mode_scores": result.mode_scores,
            "verdict": verdict,
            "top_attributions": [
                {"step": a.step, "drop": round(a.score_drop, 3),
                 "modes": a.top_modes}
                for a in result.per_step[:5]
            ],
        }
        with open(args.json, "w") as f:
            json.dump(payload, f, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
