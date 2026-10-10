"""agent-heal: self-healing supervisor for long-running agent/training jobs.

Classify the failure, pick the recovery — not dumb retries.
"""

from .supervisor import Supervisor, SupervisionResult
from .taxonomy import FailureTaxonomy, FailureEntry
from .classifier import classify, Classification
from .policies import decide, PolicyDecision, POLICIES
from .checkpoint import ProgressWriter, read_completed, progress_path_from_env
from .heartbeat import HeartbeatWriter, HeartbeatMonitor
from .incidents import Incident, IncidentLog

__all__ = [
    "Supervisor", "SupervisionResult",
    "FailureTaxonomy", "FailureEntry",
    "classify", "Classification",
    "decide", "PolicyDecision", "POLICIES",
    "ProgressWriter", "read_completed", "progress_path_from_env",
    "HeartbeatWriter", "HeartbeatMonitor",
    "Incident", "IncidentLog",
]

__version__ = "0.1.0"
