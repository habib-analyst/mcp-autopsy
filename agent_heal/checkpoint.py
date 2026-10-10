"""Checkpoint-resume protocol.

Convention (generalized from medmodel-discovery's resilient runner):
  - The supervisor sets AGENT_HEAL_PROGRESS=<path> in the job's environment.
  - The job appends one JSON record per completed unit of work, fsync'd:
        {"unit_id": "<stable id>", ...metrics}
  - On (re)start the job reads the file and SKIPS units already recorded.
  - Error records ({"status": "error", ...}) do NOT count as completed —
    they are retried.

This is the mechanism that makes resume cheaper than naive retry: a killed
job loses only its in-flight unit, never the completed ones.
"""

from __future__ import annotations

import json
import os


def read_completed(path: str) -> set[str]:
    """Unit ids with a successful (non-error) record."""
    done: set[str] = set()
    if not path or not os.path.exists(path):
        return done
    with open(path) as f:
        for line in f:
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if r.get("status") == "error":
                continue
            uid = r.get("unit_id")
            if uid is not None:
                done.add(str(uid))
    return done


class ProgressWriter:
    """fsync-per-record JSONL writer. Survives SIGKILL mid-matrix."""

    def __init__(self, path: str):
        self.path = path
        d = os.path.dirname(os.path.abspath(path))
        os.makedirs(d, exist_ok=True)

    def log(self, record: dict) -> None:
        with open(self.path, "a") as f:
            f.write(json.dumps(record) + "\n")
            f.flush()
            os.fsync(f.fileno())

    def error(self, unit_id: str, exc: BaseException) -> None:
        self.log({"unit_id": str(unit_id), "status": "error",
                  "error": f"{type(exc).__name__}: {exc}"})


def progress_path_from_env(default: str = "progress.jsonl") -> str:
    return os.environ.get("AGENT_HEAL_PROGRESS", default)
