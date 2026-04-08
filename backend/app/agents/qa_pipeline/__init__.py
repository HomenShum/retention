"""
QA Pipeline - 3-stage autonomous QA pipeline

Stages:
1. Crawl Agent (gpt-5-mini) - BFS app crawling with device tools
2. Workflow Agent (gpt-5.4) - Pure reasoning: crawl data -> user workflows
3. Test Case Agent (gpt-5.4) - Pure reasoning: workflows -> 20+ test cases
"""

from .schemas import (
    CrawlResult,
    ScreenNode,
    ComponentInfo,
    ScreenTransition,
    WorkflowResult,
    Workflow,
    WorkflowStep,
    TestSuiteResult,
    TestCase,
    TestStep,
    WorkflowSummary,
)
# Lazy imports: agent modules depend on OpenAI Agents SDK which can be slow on cold start.
# QAPipelineService is imported eagerly since main.py needs it, but the agent creator
# functions are only needed when a pipeline actually runs.
def create_crawl_agent(*args, **kwargs):
    from .crawl_agent import create_crawl_agent as _fn
    return _fn(*args, **kwargs)

def create_workflow_agent(*args, **kwargs):
    from .workflow_agent import create_workflow_agent as _fn
    return _fn(*args, **kwargs)

def create_testcase_agent(*args, **kwargs):
    from .testcase_agent import create_testcase_agent as _fn
    return _fn(*args, **kwargs)

from .qa_pipeline_service import QAPipelineService

# Lazy imports for ROP system (avoid import overhead on cold start)
def suggest_next(*args, **kwargs):
    from .suggest_next import suggest_next as _fn
    return _fn(*args, **kwargs)

def check_divergence(*args, **kwargs):
    from .suggest_next import check_divergence as _fn
    return _fn(*args, **kwargs)

def get_rop_registry():
    from .rop_manifest import get_rop_registry as _fn
    return _fn()

__all__ = [
    "CrawlResult",
    "ScreenNode",
    "ComponentInfo",
    "ScreenTransition",
    "WorkflowResult",
    "Workflow",
    "WorkflowStep",
    "TestSuiteResult",
    "TestCase",
    "TestStep",
    "WorkflowSummary",
    "create_crawl_agent",
    "create_workflow_agent",
    "create_testcase_agent",
    "QAPipelineService",
    "suggest_next",
    "check_divergence",
    "get_rop_registry",
]
