"""agent-autopsy: post-mortem forensics for failed agent runs.

Given a trace of an agent run (tool-call logs), agent-autopsy answers:
  1. WHICH step poisoned the trajectory (causal attribution by ablation)
  2. WHAT went wrong (failure-mode taxonomy)
  3. WHETHER it is safe to resume, and from where (resume-safety verdict)

Offline-first: pure stdlib, no model calls, no network. $0.
"""

__version__ = "0.1.0"

from .ingest import ingest, ingest_jsonl, ingest_openai, ingest_anthropic
from .attribution import attribute, failure_score, AutopsyResult
from .taxonomy import MODES, detect_all
from .resume import resume_verdict
from .report import text_report, html_report

__all__ = [
    "ingest", "ingest_jsonl", "ingest_openai", "ingest_anthropic",
    "attribute", "failure_score", "AutopsyResult",
    "MODES", "detect_all",
    "resume_verdict",
    "text_report", "html_report",
]
