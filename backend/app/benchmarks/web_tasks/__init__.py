"""Web benchmark tasks — registry, runner, and executors."""

from .task_registry import WebTaskRegistry, BenchmarkTask, TaskBucket

__all__ = ["WebTaskRegistry", "BenchmarkTask", "TaskBucket"]
