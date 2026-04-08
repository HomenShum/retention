"""
Evidence Writer Service.

Persists BenchmarkRunEvidence JSON files and manages artifact directories.
Mirrors the TrajectoryLogger save/load/list pattern.
"""

import json
import logging
import os
from pathlib import Path
from typing import Dict, List, Optional

from .evidence_schema import BenchmarkRunEvidence, AgentMode

logger = logging.getLogger(__name__)

DEFAULT_RUNS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
    "data",
    "benchmark_runs",
)


class EvidenceWriter:
    """Write, read, and list benchmark run evidence on disk."""

    def __init__(self, base_dir: str = DEFAULT_RUNS_DIR):
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)

    # ── paths ────────────────────────────────────────────────

    def _suite_dir(self, suite_id: str) -> Path:
        return self.base_dir / suite_id

    def _task_dir(self, suite_id: str, task_id: str, mode: AgentMode) -> Path:
        return self._suite_dir(suite_id) / "tasks" / task_id / mode.value

    def _evidence_path(self, suite_id: str, task_id: str, mode: AgentMode) -> Path:
        return self._task_dir(suite_id, task_id, mode) / "evidence.json"

    # ── write ────────────────────────────────────────────────

    def save_evidence(
        self,
        suite_id: str,
        evidence: BenchmarkRunEvidence,
    ) -> str:
        """Save a single run's evidence. Returns the file path."""
        task_dir = self._task_dir(suite_id, evidence.task_id, evidence.agent_mode)
        task_dir.mkdir(parents=True, exist_ok=True)

        # Create sub-dirs for artifacts
        (task_dir / "screenshots").mkdir(exist_ok=True)

        path = self._evidence_path(suite_id, evidence.task_id, evidence.agent_mode)
        path.write_text(evidence.model_dump_json(indent=2))
        logger.info(f"[EvidenceWriter] Saved evidence → {path}")
        return str(path)

    def save_suite_manifest(
        self, suite_id: str, manifest: Dict
    ) -> str:
        """Save suite-level manifest.json."""
        suite_dir = self._suite_dir(suite_id)
        suite_dir.mkdir(parents=True, exist_ok=True)
        path = suite_dir / "manifest.json"
        path.write_text(json.dumps(manifest, indent=2, default=str))
        return str(path)

    def save_scorecard(self, suite_id: str, scorecard: Dict) -> str:
        """Save suite-level scorecard.json."""
        suite_dir = self._suite_dir(suite_id)
        suite_dir.mkdir(parents=True, exist_ok=True)
        path = suite_dir / "scorecard.json"
        path.write_text(json.dumps(scorecard, indent=2, default=str))
        return str(path)

    # ── read ─────────────────────────────────────────────────

    def load_evidence(
        self, suite_id: str, task_id: str, mode: AgentMode
    ) -> Optional[BenchmarkRunEvidence]:
        path = self._evidence_path(suite_id, task_id, mode)
        if not path.exists():
            return None
        return BenchmarkRunEvidence.model_validate_json(path.read_text())

    def load_suite_manifest(self, suite_id: str) -> Optional[Dict]:
        path = self._suite_dir(suite_id) / "manifest.json"
        if not path.exists():
            return None
        return json.loads(path.read_text())

    def load_scorecard(self, suite_id: str) -> Optional[Dict]:
        path = self._suite_dir(suite_id) / "scorecard.json"
        if not path.exists():
            return None
        return json.loads(path.read_text())

    # ── list ─────────────────────────────────────────────────

    def list_suites(self) -> List[str]:
        """List all suite IDs (directory names)."""
        if not self.base_dir.exists():
            return []
        return sorted(
            d.name
            for d in self.base_dir.iterdir()
            if d.is_dir() and (d / "manifest.json").exists()
        )

    def list_task_evidences(
        self, suite_id: str
    ) -> List[BenchmarkRunEvidence]:
        """Load all evidence files for a suite."""
        tasks_dir = self._suite_dir(suite_id) / "tasks"
        if not tasks_dir.exists():
            return []

        evidences: List[BenchmarkRunEvidence] = []
        for task_dir in sorted(tasks_dir.iterdir()):
            if not task_dir.is_dir():
                continue
            for mode_dir in task_dir.iterdir():
                if not mode_dir.is_dir():
                    continue
                evidence_file = mode_dir / "evidence.json"
                if evidence_file.exists():
                    try:
                        ev = BenchmarkRunEvidence.model_validate_json(
                            evidence_file.read_text()
                        )
                        evidences.append(ev)
                    except Exception as e:
                        logger.warning(f"Failed to load {evidence_file}: {e}")
        return evidences

    def artifacts_dir(self, suite_id: str, task_id: str, mode: AgentMode) -> Path:
        """Return the artifact directory for a task run, creating it if needed."""
        d = self._task_dir(suite_id, task_id, mode)
        d.mkdir(parents=True, exist_ok=True)
        return d
