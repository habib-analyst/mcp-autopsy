"""mcp-autopsy: agent forensics as MCP tools.

Wraps Habib Ur Rehman's agent-autopsy (post-mortem trace forensics) and
agent-heal (self-healing job supervisor) as Model Context Protocol tools
for Claude Code / Cursor.

Vendored packages:
  agent_autopsy/  from habib-analyst/agent-autopsy (MIT) — failure
                  attribution over agent traces + resume-safety verdicts
  agent_heal/     from habib-analyst/agent-heal (MIT) — supervised job
                  execution with failure classification + recovery
"""
from .core import diagnose, verdict, heal
from .server import mcp, main

__all__ = ["diagnose", "verdict", "heal", "mcp", "main"]
__version__ = "0.1.0"
