"""
LangSmith Tracing Integration

Provides observability for agent executions via LangSmith.
Enables tracing of OpenAI calls, agent handoffs, and tool executions.

Usage:
1. Set LANGSMITH_API_KEY environment variable
2. Import get_traced_client() for traced OpenAI calls
3. Use @traceable decorator for custom functions
"""

import os
import logging
from typing import Optional, Any
from functools import wraps

logger = logging.getLogger(__name__)

# LangSmith configuration
LANGSMITH_ENABLED = False

try:
    from langsmith import traceable
    from langsmith.wrappers import wrap_openai
    LANGSMITH_AVAILABLE = True
except ImportError:
    LANGSMITH_AVAILABLE = False
    traceable = None
    wrap_openai = None
    logger.warning("LangSmith not installed. Run: pip install langsmith")


def init_langsmith() -> bool:
    """
    Initialize LangSmith tracing if API key is available.
    
    Returns:
        True if LangSmith is enabled, False otherwise
    """
    global LANGSMITH_ENABLED
    
    api_key = os.getenv("LANGSMITH_API_KEY")
    
    if not LANGSMITH_AVAILABLE:
        logger.info("LangSmith package not available")
        return False
    
    if not api_key:
        logger.info("LANGSMITH_API_KEY not set - tracing disabled")
        return False
    
    # Enable LangSmith tracing
    os.environ["LANGSMITH_TRACING"] = "true"
    LANGSMITH_ENABLED = True
    
    # Set project name if not already set
    if not os.getenv("LANGSMITH_PROJECT"):
        os.environ["LANGSMITH_PROJECT"] = "retention"
    
    logger.info(f"✅ LangSmith tracing enabled (project: {os.getenv('LANGSMITH_PROJECT')})")
    return True


def get_traced_client(client: Any) -> Any:
    """
    Wrap an OpenAI client with LangSmith tracing.
    
    Args:
        client: OpenAI or AsyncOpenAI client instance
        
    Returns:
        Wrapped client if LangSmith enabled, original client otherwise
    """
    # Lazy init so CLI/scripts that don't go through FastAPI startup still get
    # tracing if LANGSMITH_API_KEY is present.
    if not LANGSMITH_ENABLED:
        try:
            init_langsmith()
        except Exception:
            # Never block app behavior on tracing
            return client

    if not LANGSMITH_ENABLED or not wrap_openai:
        return client
    
    try:
        wrapped = wrap_openai(client)
        logger.debug("OpenAI client wrapped with LangSmith tracing")
        return wrapped
    except Exception as e:
        logger.warning(f"Failed to wrap OpenAI client: {e}")
        return client


def trace_function(name: Optional[str] = None, run_type: str = "chain"):
    """
    Decorator to trace a function with LangSmith.
    Falls back to no-op if LangSmith is not available.
    
    Args:
        name: Optional custom name for the trace
        run_type: Type of run (chain, tool, llm, retriever)
    """
    def decorator(func):
        if not LANGSMITH_ENABLED or not traceable:
            return func
        
        trace_name = name or func.__name__
        return traceable(name=trace_name, run_type=run_type)(func)
    
    return decorator


def trace_agent_execution(agent_name: str, task_description: str):
    """
    Context manager for tracing agent executions.
    
    Args:
        agent_name: Name of the agent
        task_description: Description of the task
    """
    if not LANGSMITH_ENABLED or not traceable:
        # Return a no-op context manager
        from contextlib import nullcontext
        return nullcontext()
    
    # Use traceable as a context decorator
    @traceable(name=f"agent:{agent_name}", run_type="chain")
    def _traced_execution(task: str):
        return {"agent": agent_name, "task": task}
    
    return _traced_execution(task_description)


# Export convenience functions
def is_tracing_enabled() -> bool:
    """Check if LangSmith tracing is enabled."""
    return LANGSMITH_ENABLED


__all__ = [
    "init_langsmith",
    "get_traced_client",
    "trace_function",
    "trace_agent_execution",
    "is_tracing_enabled",
    "LANGSMITH_ENABLED",
]

