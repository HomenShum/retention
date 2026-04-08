"""
Web Benchmark Task Registry.

Mirrors the AndroidWorldTaskRegistry interface: list_tasks(), get(), count.
Tasks are loaded from backend/data/benchmark_tasks.json.
"""

import json
import logging
import os
from enum import Enum
from typing import Dict, List, Optional, Any

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

DEFAULT_TASKS_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))),
    "data",
    "benchmark_tasks.json",
)


class TaskBucket(str, Enum):
    LOGIN_AUTH = "login_auth"
    NAVIGATION_STATE = "navigation_state"
    FORM_SUBMIT = "form_submit"
    ERROR_RETRY = "error_retry"
    VISUAL_UI = "visual_ui"


class BenchmarkTask(BaseModel):
    """A single web benchmark task definition."""

    task_id: str = Field(..., description="Unique task identifier, e.g. login-001")
    app_id: str = Field(..., description="Target app identifier")
    bucket: TaskBucket = Field(..., description="Task category bucket")
    platform: str = Field("web", description="web or android-emulator")
    prompt: str = Field(..., description="Natural-language instruction for the agent")
    expected_outcome: str = Field(
        ..., description="Deterministic pass criteria"
    )
    required_evidence: List[str] = Field(
        default_factory=list,
        description="Evidence types required: screenshot, trace, video, etc.",
    )
    pass_rule: str = Field(
        ..., description="How to determine pass/fail"
    )
    timeout_seconds: int = Field(60, description="Max execution time")
    max_reruns: int = Field(1, description="Max retry attempts")
    base_url: str = Field("", description="App base URL for web tasks")
    setup_steps: List[str] = Field(
        default_factory=list, description="Pre-conditions or setup actions"
    )
    teardown_steps: List[str] = Field(
        default_factory=list, description="Cleanup actions after the task"
    )
    element_intents: List[str] = Field(
        default_factory=list,
        description="Natural-language intent descriptions for self-healing element resolution",
    )

    def to_dict(self) -> Dict[str, Any]:
        return self.model_dump()


class WebTaskRegistry:
    """Registry for web benchmark tasks. Loads from JSON file."""

    def __init__(self, tasks_file: str = DEFAULT_TASKS_FILE):
        self._tasks: Dict[str, BenchmarkTask] = {}
        self._tasks_file = tasks_file
        self._load()

    def _load(self):
        if not os.path.exists(self._tasks_file):
            logger.warning(f"Tasks file not found: {self._tasks_file}")
            return
        try:
            with open(self._tasks_file) as f:
                data = json.load(f)
            for item in data.get("tasks", []):
                task = BenchmarkTask(**item)
                self._tasks[task.task_id] = task
            logger.info(f"Loaded {len(self._tasks)} benchmark tasks from {self._tasks_file}")
        except Exception as e:
            logger.error(f"Failed to load tasks: {e}")

    @property
    def count(self) -> int:
        return len(self._tasks)

    def get(self, task_id: str) -> Optional[BenchmarkTask]:
        return self._tasks.get(task_id)

    def list_tasks(
        self,
        bucket: Optional[TaskBucket] = None,
        app_id: Optional[str] = None,
    ) -> List[BenchmarkTask]:
        tasks = list(self._tasks.values())
        if bucket:
            tasks = [t for t in tasks if t.bucket == bucket]
        if app_id:
            tasks = [t for t in tasks if t.app_id == app_id]
        return tasks

    def list_task_ids(self) -> List[str]:
        return sorted(self._tasks.keys())

    def list_buckets(self) -> List[str]:
        return [b.value for b in TaskBucket]

    def list_apps(self) -> List[str]:
        return sorted(set(t.app_id for t in self._tasks.values()))
