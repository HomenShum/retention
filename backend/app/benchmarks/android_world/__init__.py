"""
AndroidWorld Benchmark Integration.

Provides adapters for running AndroidWorld tasks on our Mobile MCP infrastructure.
Based on: https://github.com/google-research/android_world

AndroidWorld is a dynamic benchmarking environment with 116 hand-crafted tasks
across 20 real-world Android apps for evaluating autonomous device agents.
"""

from .task_registry import (
    AndroidWorldTask,
    AndroidWorldTaskRegistry,
    TaskDifficulty,
    TaskCategory,
)
from .executor import AndroidWorldExecutor

__all__ = [
    "AndroidWorldTask",
    "AndroidWorldTaskRegistry",
    "AndroidWorldExecutor",
    "TaskDifficulty",
    "TaskCategory",
]

