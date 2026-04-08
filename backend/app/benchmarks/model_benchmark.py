"""Model Benchmark — compare free models head-to-head on retention.sh tasks.

Runs each model in the OpenRouter rotation pool through the same set of
benchmark tasks via NemoClaw's instrumented runner, then scores and ranks
them on tool accuracy, correctness, latency, and throughput.

Usage (API):
  POST /api/benchmarks/model-compare/run
  GET  /api/benchmarks/model-compare/runs/{run_id}

Usage (MCP):
  ta.benchmark.model_compare  { "tasks": ["list_apps"], "models": 3 }
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Benchmark Task Definitions
# ---------------------------------------------------------------------------

@dataclass
class BenchmarkTask:
    """A single benchmark task with ground-truth expectations."""
    task_id: str
    name: str
    prompt: str
    expected_tools: list[str]          # MCP tool names the model should call
    expected_keywords: list[str]       # Keywords that should appear in the response
    category: str                       # single_tool | multi_tool | reasoning | pipeline
    timeout_s: int = 60
    max_turns: int = 10


# The canonical task suite — covers the key retention.sh workflows
BENCHMARK_TASKS: dict[str, BenchmarkTask] = {}

def _register(t: BenchmarkTask) -> BenchmarkTask:
    BENCHMARK_TASKS[t.task_id] = t
    return t

# ===================================================================
# CATEGORY 1: SINGLE TOOL (8 tasks)
# Can the model pick the right tool and pass correct arguments?
# ===================================================================

_register(BenchmarkTask(
    task_id="st_list_apps",
    name="List Demo Apps",
    prompt="List all available demo apps in the catalog. Return their IDs and names.",
    expected_tools=["ta.pipeline.list_apps"],
    expected_keywords=["google-contacts", "instagram"],
    category="single_tool",
    timeout_s=30,
    max_turns=5,
))

_register(BenchmarkTask(
    task_id="st_system_check",
    name="System Health Check",
    prompt="Run a system health check and report the status of each component.",
    expected_tools=["ta.system_check"],
    expected_keywords=["backend", "status"],
    category="single_tool",
    timeout_s=30,
    max_turns=5,
))

_register(BenchmarkTask(
    task_id="st_device_list",
    name="Device Inventory",
    prompt="List all available Android devices and emulators. Report their IDs and connection status.",
    expected_tools=["ta.device.list"],
    expected_keywords=["device", "emulator"],
    category="single_tool",
    timeout_s=30,
    max_turns=5,
))

_register(BenchmarkTask(
    task_id="st_connection_info",
    name="Server Connection Info",
    prompt="Get the server connection info including version and pipeline readiness.",
    expected_tools=["ta.meta.connection_info"],
    expected_keywords=["version", "url"],
    category="single_tool",
    timeout_s=30,
    max_turns=5,
))

_register(BenchmarkTask(
    task_id="st_git_status",
    name="Git Status",
    prompt="Show the current git status of the repository — modified, staged, and untracked files.",
    expected_tools=["ta.codebase.git_status"],
    expected_keywords=["modified", "file"],
    category="single_tool",
    timeout_s=30,
    max_turns=5,
))

_register(BenchmarkTask(
    task_id="st_recent_commits",
    name="Recent Commits",
    prompt="Get the 10 most recent git commits. Show the SHA, author, and message for each.",
    expected_tools=["ta.codebase.recent_commits"],
    expected_keywords=["commit", "author"],
    category="single_tool",
    timeout_s=30,
    max_turns=5,
))

_register(BenchmarkTask(
    task_id="st_web_discover",
    name="Web Element Discovery",
    prompt="Discover all testable elements on https://example.com and list what you find.",
    expected_tools=["ta.playwright.discover"],
    expected_keywords=["element", "link"],
    category="single_tool",
    timeout_s=60,
    max_turns=6,
))

_register(BenchmarkTask(
    task_id="st_file_tree",
    name="File Tree",
    prompt="Show the file tree for the backend/app directory. List the top-level folders.",
    expected_tools=["ta.codebase.file_tree"],
    expected_keywords=["agents", "api", "services"],
    category="single_tool",
    timeout_s=30,
    max_turns=5,
))

# ===================================================================
# CATEGORY 2: MULTI-TOOL CHAINS (8 tasks)
# Can the model sequence 2-3 tools in the right order?
# ===================================================================

_register(BenchmarkTask(
    task_id="mt_health_then_devices",
    name="Health + Device List",
    prompt=(
        "First check the system health, then list all available devices. "
        "Summarize both results together."
    ),
    expected_tools=["ta.system_check", "ta.device.list"],
    expected_keywords=["device", "status"],
    category="multi_tool",
    timeout_s=45,
    max_turns=8,
))

_register(BenchmarkTask(
    task_id="mt_search_then_read",
    name="Search + Read File",
    prompt=(
        "Search the codebase for the file 'main.py' in the backend, "
        "then read it and tell me how many route files are imported."
    ),
    expected_tools=["ta.codebase.search", "ta.codebase.read_file"],
    expected_keywords=["import", "route"],
    category="multi_tool",
    timeout_s=60,
    max_turns=10,
))

_register(BenchmarkTask(
    task_id="mt_commits_then_diff",
    name="Commits + Diff Inspection",
    prompt=(
        "Get the 5 most recent commits, then show the diff for the most recent one. "
        "Summarize what files changed."
    ),
    expected_tools=["ta.codebase.recent_commits", "ta.codebase.commit_diff"],
    expected_keywords=["commit", "changed"],
    category="multi_tool",
    timeout_s=60,
    max_turns=10,
))

_register(BenchmarkTask(
    task_id="mt_discover_then_test",
    name="Discover + Test Element",
    prompt=(
        "Discover the interactive elements on https://example.com, "
        "then test clicking the 'More information...' link."
    ),
    expected_tools=["ta.playwright.discover", "ta.playwright.test_interaction"],
    expected_keywords=["link", "click"],
    category="multi_tool",
    timeout_s=90,
    max_turns=10,
))

_register(BenchmarkTask(
    task_id="mt_apps_then_catalog_run",
    name="List Apps + Run Catalog",
    prompt=(
        "List all demo apps, then start a QA pipeline run on the google-contacts app "
        "from the catalog."
    ),
    expected_tools=["ta.pipeline.list_apps", "ta.pipeline.run_catalog"],
    expected_keywords=["google-contacts", "run_id"],
    category="multi_tool",
    timeout_s=90,
    max_turns=10,
))

_register(BenchmarkTask(
    task_id="mt_tree_then_list",
    name="File Tree + Directory List",
    prompt=(
        "Get the file tree for the backend directory, "
        "then list the contents of the backend/app/agents directory."
    ),
    expected_tools=["ta.codebase.file_tree", "ta.codebase.list_directory"],
    expected_keywords=["agents", "coordinator"],
    category="multi_tool",
    timeout_s=45,
    max_turns=8,
))

_register(BenchmarkTask(
    task_id="mt_health_page_check",
    name="Health + Page Health",
    prompt=(
        "Run a system check, then also run a page health check on http://localhost:5173. "
        "Compare the results — is the backend healthy but the frontend down, or vice versa?"
    ),
    expected_tools=["ta.system_check", "ta.playwright.check_page_health"],
    expected_keywords=["health", "page"],
    category="multi_tool",
    timeout_s=60,
    max_turns=8,
))

_register(BenchmarkTask(
    task_id="mt_nemoclaw_status_telemetry",
    name="NemoClaw Status + Telemetry",
    prompt=(
        "Check NemoClaw's status and then get the telemetry data. "
        "Report which model is active and its performance metrics."
    ),
    expected_tools=["ta.nemoclaw.status", "ta.nemoclaw.telemetry"],
    expected_keywords=["model", "latency"],
    category="multi_tool",
    timeout_s=45,
    max_turns=8,
))

# ===================================================================
# CATEGORY 3: REASONING + ANALYSIS (6 tasks)
# Can the model interpret tool output and derive correct conclusions?
# ===================================================================

_register(BenchmarkTask(
    task_id="reason_count_routes",
    name="Count API Routes",
    prompt=(
        "Read the file backend/app/main.py and count how many router includes "
        "(app.include_router calls) there are. Return the exact number."
    ),
    expected_tools=["ta.codebase.read_file"],
    expected_keywords=["include_router"],
    category="reasoning",
    timeout_s=60,
    max_turns=10,
))

_register(BenchmarkTask(
    task_id="reason_find_imports",
    name="Trace Import Chain",
    prompt=(
        "Search for 'NemoClawAgent' in the codebase. Read the file that defines it, "
        "then list all classes it depends on (imports from the same package)."
    ),
    expected_tools=["ta.codebase.search", "ta.codebase.read_file"],
    expected_keywords=["NemotronClient", "DeepAgentBridge"],
    category="reasoning",
    timeout_s=60,
    max_turns=10,
))

_register(BenchmarkTask(
    task_id="reason_golden_bugs",
    name="Analyze Golden Bugs",
    prompt=(
        "Read the golden bugs file at backend/data/golden_bugs.json. "
        "How many bugs are there? What severity levels exist? Which bug IDs are 'critical'?"
    ),
    expected_tools=["ta.codebase.read_file"],
    expected_keywords=["GOLDEN", "critical", "severity"],
    category="reasoning",
    timeout_s=60,
    max_turns=10,
))

_register(BenchmarkTask(
    task_id="reason_python_analysis",
    name="Python Data Analysis",
    prompt=(
        "Use exec_python to calculate: given 10 golden bugs, if a model correctly "
        "identifies 7 with 1 false positive, what are the precision, recall, and F1 score? "
        "Return the numbers."
    ),
    expected_tools=["ta.codebase.exec_python"],
    expected_keywords=["precision", "recall", "f1"],
    category="reasoning",
    timeout_s=60,
    max_turns=8,
))

_register(BenchmarkTask(
    task_id="reason_shell_wc",
    name="Shell Word Count",
    prompt=(
        "Use shell_command to count the number of lines in backend/app/api/mcp_server.py. "
        "Report the exact line count."
    ),
    expected_tools=["ta.codebase.shell_command"],
    expected_keywords=["line"],
    category="reasoning",
    timeout_s=45,
    max_turns=8,
))

_register(BenchmarkTask(
    task_id="reason_compare_files",
    name="Compare Two Files",
    prompt=(
        "Read both backend/app/agents/coordinator/coordinator_instructions.py "
        "and backend/app/main.py. Which file is longer? By how many lines?"
    ),
    expected_tools=["ta.codebase.read_file"],
    expected_keywords=["lines", "longer"],
    category="reasoning",
    timeout_s=60,
    max_turns=10,
))

# ===================================================================
# CATEGORY 4: PIPELINE ORCHESTRATION (6 tasks)
# Can the model drive end-to-end QA workflows?
# ===================================================================

_register(BenchmarkTask(
    task_id="pipe_catalog_run_status",
    name="Pipeline: Run + Poll Status",
    prompt=(
        "Start a QA pipeline run on the 'google-contacts' catalog app, "
        "then immediately check its status and report back."
    ),
    expected_tools=["ta.pipeline.run_catalog", "ta.pipeline.status"],
    expected_keywords=["run_id", "status"],
    category="pipeline",
    timeout_s=90,
    max_turns=10,
))

_register(BenchmarkTask(
    task_id="pipe_web_flow",
    name="Pipeline: Web Flow",
    prompt=(
        "Run a full QA web flow on https://example.com. Report the run_id."
    ),
    expected_tools=["ta.run_web_flow"],
    expected_keywords=["run_id"],
    category="pipeline",
    timeout_s=90,
    max_turns=8,
))

_register(BenchmarkTask(
    task_id="pipe_results_list",
    name="Pipeline: List Results",
    prompt=(
        "List all completed pipeline results. How many runs are there? "
        "What was the most recent one?"
    ),
    expected_tools=["ta.pipeline.results"],
    expected_keywords=["run"],
    category="pipeline",
    timeout_s=45,
    max_turns=8,
))

_register(BenchmarkTask(
    task_id="pipe_batch_test",
    name="Pipeline: Batch Test",
    prompt=(
        "Run a batch test on https://example.com with a maximum of 5 interactions. "
        "Report any issues found."
    ),
    expected_tools=["ta.playwright.batch_test"],
    expected_keywords=["test", "interaction"],
    category="pipeline",
    timeout_s=120,
    max_turns=10,
))

_register(BenchmarkTask(
    task_id="pipe_run_then_verdict",
    name="Pipeline: Run + Verdict",
    prompt=(
        "Start a web flow on https://example.com, then emit a pass/fail verdict "
        "for the run with a 0.7 pass threshold."
    ),
    expected_tools=["ta.run_web_flow", "ta.emit_verdict"],
    expected_keywords=["verdict", "pass"],
    category="pipeline",
    timeout_s=120,
    max_turns=12,
))

_register(BenchmarkTask(
    task_id="pipe_scoped_crawl",
    name="Pipeline: Scoped Crawl",
    prompt=(
        "Start a QA pipeline on https://example.com but scope the crawl to only the "
        "homepage using entry_url='/' and max_crawl_turns=10. Use scope_hint 'Only test the main page links'."
    ),
    expected_tools=["ta.pipeline.run"],
    expected_keywords=["run_id", "scope"],
    category="pipeline",
    timeout_s=90,
    max_turns=10,
))

# ===================================================================
# CATEGORY 5: ERROR RECOVERY (6 tasks)
# Can the model handle failures, missing data, and bad input gracefully?
# ===================================================================

_register(BenchmarkTask(
    task_id="err_bad_run_id",
    name="Error: Invalid Run ID",
    prompt=(
        "Get the status of pipeline run_id 'nonexistent-12345'. "
        "If it doesn't exist, say so clearly."
    ),
    expected_tools=["ta.pipeline.status"],
    expected_keywords=["not found", "error", "invalid", "exist"],
    category="error_recovery",
    timeout_s=30,
    max_turns=6,
))

_register(BenchmarkTask(
    task_id="err_bad_file_path",
    name="Error: Missing File",
    prompt=(
        "Read the file backend/nonexistent/fake_module.py. "
        "If the file doesn't exist, explain what happened and suggest how to find it."
    ),
    expected_tools=["ta.codebase.read_file"],
    expected_keywords=["not found", "error", "exist", "search"],
    category="error_recovery",
    timeout_s=30,
    max_turns=8,
))

_register(BenchmarkTask(
    task_id="err_bad_app_id",
    name="Error: Unknown Catalog App",
    prompt=(
        "Start a pipeline run on the catalog app 'nonexistent-app-xyz'. "
        "Handle the error and list what apps are actually available."
    ),
    expected_tools=["ta.pipeline.run_catalog", "ta.pipeline.list_apps"],
    expected_keywords=["error", "available"],
    category="error_recovery",
    timeout_s=60,
    max_turns=10,
))

_register(BenchmarkTask(
    task_id="err_python_syntax",
    name="Error: Python Syntax Error",
    prompt=(
        "Execute this Python code: 'def foo(: return 42'. "
        "When it fails, fix the syntax and run the corrected version."
    ),
    expected_tools=["ta.codebase.exec_python"],
    expected_keywords=["syntax", "42"],
    category="error_recovery",
    timeout_s=45,
    max_turns=10,
))

_register(BenchmarkTask(
    task_id="err_shell_blocked",
    name="Error: Blocked Shell Command",
    prompt=(
        "Try running 'rm -rf /tmp/test' as a shell command. "
        "When it's blocked, explain why and suggest a safe alternative "
        "to count files instead."
    ),
    expected_tools=["ta.codebase.shell_command"],
    expected_keywords=["blocked", "not allowed", "safe"],
    category="error_recovery",
    timeout_s=30,
    max_turns=8,
))

_register(BenchmarkTask(
    task_id="err_fallback_search",
    name="Error: Search Fallback",
    prompt=(
        "Search for 'ZZZ_NONEXISTENT_CLASS_999' in the codebase. "
        "When nothing is found, broaden your search to find something "
        "related to 'agent' instead and report what you found."
    ),
    expected_tools=["ta.codebase.search"],
    expected_keywords=["agent", "no results", "not found", "found"],
    category="error_recovery",
    timeout_s=60,
    max_turns=10,
))

# ===================================================================
# CATEGORY 6: MULTI-STEP DEBUGGING (6 tasks)
# Can the model investigate, diagnose, and form conclusions?
# ===================================================================

_register(BenchmarkTask(
    task_id="debug_find_handler",
    name="Debug: Find Route Handler",
    prompt=(
        "Find which file handles the /api/benchmarks endpoint. "
        "Search for the route, read the handler file, and explain what it does."
    ),
    expected_tools=["ta.codebase.search", "ta.codebase.read_file"],
    expected_keywords=["benchmark", "route", "handler"],
    category="debugging",
    timeout_s=60,
    max_turns=10,
))

_register(BenchmarkTask(
    task_id="debug_trace_error",
    name="Debug: Trace Error Import",
    prompt=(
        "Search the codebase for 'agent_error_log'. Read the file and identify "
        "what errors have been logged. Summarize the root causes."
    ),
    expected_tools=["ta.codebase.search", "ta.codebase.read_file"],
    expected_keywords=["error", "log"],
    category="debugging",
    timeout_s=60,
    max_turns=10,
))

_register(BenchmarkTask(
    task_id="debug_env_check",
    name="Debug: Environment Variables",
    prompt=(
        "Use exec_python to check if OPENROUTER_API_KEY is set in the environment "
        "(just check existence, don't print the value). Also check NVIDIA_API_KEY "
        "and report which ones are configured."
    ),
    expected_tools=["ta.codebase.exec_python"],
    expected_keywords=["OPENROUTER", "configured", "set"],
    category="debugging",
    timeout_s=45,
    max_turns=8,
))

_register(BenchmarkTask(
    task_id="debug_mcp_tools_count",
    name="Debug: Count MCP Tools",
    prompt=(
        "Search the mcp_server.py file to count how many MCP tools are registered. "
        "Use shell_command with grep to count lines matching 'MCPTool(' in the file."
    ),
    expected_tools=["ta.codebase.shell_command"],
    expected_keywords=["MCPTool", "count"],
    category="debugging",
    timeout_s=45,
    max_turns=8,
))

_register(BenchmarkTask(
    task_id="debug_dependency_chain",
    name="Debug: Dependency Chain",
    prompt=(
        "The NemoClaw agent depends on OpenRouterRotation. Find where OpenRouterRotation "
        "is defined, read its imports, and map out its dependency chain (what external "
        "libraries does it use?)."
    ),
    expected_tools=["ta.codebase.search", "ta.codebase.read_file"],
    expected_keywords=["rotation", "import", "threading"],
    category="debugging",
    timeout_s=60,
    max_turns=12,
))

_register(BenchmarkTask(
    task_id="debug_benchmark_results",
    name="Debug: Analyze Benchmark Output",
    prompt=(
        "List the contents of backend/data/benchmark_runs/ directory. "
        "If there are any results files, read one and summarize the benchmark outcomes."
    ),
    expected_tools=["ta.codebase.list_directory", "ta.codebase.read_file"],
    expected_keywords=["benchmark", "score", "model"],
    category="debugging",
    timeout_s=60,
    max_turns=10,
))

# ===================================================================
# CATEGORY 7: FEEDBACK + ANNOTATION (4 tasks)
# Can the model interact with the feedback/annotation system?
# ===================================================================

_register(BenchmarkTask(
    task_id="fb_annotate_flag",
    name="Feedback: Flag a Test Case",
    prompt=(
        "Annotate test case 'tc_001' in run 'demo-run-001' as a 'flag' with content "
        "'This test is flaky — fails 20% of the time on slow network'."
    ),
    expected_tools=["ta.feedback.annotate"],
    expected_keywords=["flag", "annotate", "tc_001"],
    category="feedback",
    timeout_s=30,
    max_turns=6,
))

_register(BenchmarkTask(
    task_id="fb_list_annotations",
    name="Feedback: List Annotations",
    prompt=(
        "List all feedback annotations for pipeline run 'demo-run-001'. "
        "Summarize what types of feedback exist."
    ),
    expected_tools=["ta.feedback.list"],
    expected_keywords=["feedback", "annotation"],
    category="feedback",
    timeout_s=30,
    max_turns=6,
))

_register(BenchmarkTask(
    task_id="fb_summary",
    name="Feedback: Summary Report",
    prompt=(
        "Get the feedback summary for run 'demo-run-001'. "
        "How many flags, suggestions, approvals, and rejections are there?"
    ),
    expected_tools=["ta.feedback.summary"],
    expected_keywords=["summary", "count"],
    category="feedback",
    timeout_s=30,
    max_turns=6,
))

_register(BenchmarkTask(
    task_id="fb_annotate_then_verify",
    name="Feedback: Annotate + Verify",
    prompt=(
        "Add an 'approval' annotation to workflow 'wf_login' in run 'demo-run-001' "
        "with content 'Login flow verified — works correctly'. "
        "Then list annotations to confirm it was saved."
    ),
    expected_tools=["ta.feedback.annotate", "ta.feedback.list"],
    expected_keywords=["approval", "login", "saved"],
    category="feedback",
    timeout_s=45,
    max_turns=8,
))

# ===================================================================
# CATEGORY 8: ADVERSARIAL / AMBIGUOUS PROMPTS (4 tasks)
# Can the model handle vague, tricky, or multi-intent requests?
# ===================================================================

_register(BenchmarkTask(
    task_id="adv_vague_request",
    name="Adversarial: Vague Request",
    prompt="Check everything.",
    expected_tools=["ta.system_check"],
    expected_keywords=["status", "check"],
    category="adversarial",
    timeout_s=45,
    max_turns=8,
))

_register(BenchmarkTask(
    task_id="adv_multi_intent",
    name="Adversarial: Multi-Intent",
    prompt=(
        "I need to know if the system is healthy, what apps we have, "
        "and what the last few commits were. Also what devices are available."
    ),
    expected_tools=["ta.system_check", "ta.pipeline.list_apps", "ta.codebase.recent_commits", "ta.device.list"],
    expected_keywords=["status", "device", "commit"],
    category="adversarial",
    timeout_s=90,
    max_turns=15,
))

_register(BenchmarkTask(
    task_id="adv_no_tool_needed",
    name="Adversarial: No Tool Needed",
    prompt="What is the capital of France?",
    expected_tools=[],
    expected_keywords=["Paris"],
    category="adversarial",
    timeout_s=30,
    max_turns=3,
))

_register(BenchmarkTask(
    task_id="adv_wrong_tool_hint",
    name="Adversarial: Misleading Hint",
    prompt=(
        "Use the pipeline tool to search the codebase for 'FastAPI'. "
        "Note: you should use the correct codebase search tool, not the pipeline tool."
    ),
    expected_tools=["ta.codebase.search"],
    expected_keywords=["FastAPI"],
    category="adversarial",
    timeout_s=45,
    max_turns=8,
))


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

@dataclass
class TaskResult:
    """Result of running one task on one model."""
    task_id: str
    model_id: str
    response: str = ""
    tools_called: list[str] = field(default_factory=list)
    turns: int = 0
    latency_ms: float = 0.0
    total_tokens: int = 0
    tokens_per_sec: float = 0.0
    error: str | None = None

    # Scores (filled by score())
    tool_accuracy: float = 0.0     # Jaccard similarity of tools called vs expected
    keyword_hit_rate: float = 0.0  # Fraction of expected keywords found in response
    completed: bool = False        # Produced a non-empty response without error
    score: float = 0.0             # Composite: 0.4*tool + 0.3*keyword + 0.3*completed

    def compute_scores(self, task: BenchmarkTask) -> None:
        """Score this result against ground truth."""
        # Tool accuracy — Jaccard similarity
        called = set(self.tools_called)
        expected = set(task.expected_tools)
        if expected or called:
            intersection = called & expected
            union = called | expected
            self.tool_accuracy = len(intersection) / len(union) if union else 0.0
        else:
            self.tool_accuracy = 1.0

        # Keyword hit rate
        response_lower = self.response.lower()
        if task.expected_keywords:
            hits = sum(1 for kw in task.expected_keywords if kw.lower() in response_lower)
            self.keyword_hit_rate = hits / len(task.expected_keywords)
        else:
            self.keyword_hit_rate = 1.0

        # Completed
        self.completed = bool(self.response) and self.error is None

        # Composite score
        self.score = round(
            0.4 * self.tool_accuracy
            + 0.3 * self.keyword_hit_rate
            + 0.3 * (1.0 if self.completed else 0.0),
            3,
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ModelScore:
    """Aggregate score for one model across all tasks."""
    model_id: str
    tasks_run: int = 0
    avg_score: float = 0.0
    avg_tool_accuracy: float = 0.0
    avg_keyword_hit: float = 0.0
    completion_rate: float = 0.0
    avg_latency_ms: float = 0.0
    avg_tokens_per_sec: float = 0.0
    total_errors: int = 0
    category_scores: dict[str, float] = field(default_factory=dict)
    task_results: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def from_results(cls, model_id: str, results: list[TaskResult]) -> "ModelScore":
        n = len(results)
        if n == 0:
            return cls(model_id=model_id)

        # Per-category breakdown
        cat_buckets: dict[str, list[float]] = {}
        for r in results:
            task = BENCHMARK_TASKS.get(r.task_id)
            if task:
                cat_buckets.setdefault(task.category, []).append(r.score)
        cat_scores = {
            cat: round(sum(scores) / len(scores), 3)
            for cat, scores in cat_buckets.items()
        }

        return cls(
            model_id=model_id,
            tasks_run=n,
            avg_score=round(sum(r.score for r in results) / n, 3),
            avg_tool_accuracy=round(sum(r.tool_accuracy for r in results) / n, 3),
            avg_keyword_hit=round(sum(r.keyword_hit_rate for r in results) / n, 3),
            completion_rate=round(sum(1 for r in results if r.completed) / n, 3),
            avg_latency_ms=round(sum(r.latency_ms for r in results) / n, 1),
            avg_tokens_per_sec=round(
                sum(r.tokens_per_sec for r in results) / max(sum(1 for r in results if r.tokens_per_sec > 0), 1), 1
            ),
            total_errors=sum(1 for r in results if r.error),
            category_scores=cat_scores,
            task_results=[r.to_dict() for r in results],
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Benchmark Runner
# ---------------------------------------------------------------------------

# In-memory store for benchmark runs
_benchmark_runs: dict[str, dict] = {}


# All valid category names for filtering
CATEGORIES = {
    "single_tool", "multi_tool", "reasoning", "pipeline",
    "error_recovery", "debugging", "feedback", "adversarial",
}


def _select_tasks(
    task_ids: list[str] | None = None,
    categories: list[str] | None = None,
) -> list[BenchmarkTask]:
    """Resolve task selection from IDs, categories, or default (all)."""
    if task_ids:
        return [BENCHMARK_TASKS[tid] for tid in task_ids if tid in BENCHMARK_TASKS]
    if categories:
        return [t for t in BENCHMARK_TASKS.values() if t.category in categories]
    return list(BENCHMARK_TASKS.values())


async def run_model_benchmark(
    task_ids: list[str] | None = None,
    model_count: int | None = None,
    model_ids: list[str] | None = None,
    categories: list[str] | None = None,
    repeats: int = 1,
) -> dict[str, Any]:
    """Run benchmark tasks across models from the rotation pool.

    Args:
        task_ids: Specific task IDs to run (default: all 48)
        model_count: How many top models to test (default: all in pool)
        model_ids: Explicit model list (overrides model_count)
        categories: Filter by category (e.g. ["single_tool", "reasoning"])
        repeats: Run each task N times per model for variance analysis (default 1)

    Returns run metadata with run_id for polling.
    """
    from ..integrations.openrouter_rotation import get_rotation

    run_id = uuid.uuid4().hex[:8]
    rotation = get_rotation()

    # Ensure models are loaded
    if not rotation._models:
        await asyncio.get_event_loop().run_in_executor(None, rotation.refresh_models)

    # Select models
    if model_ids:
        models = model_ids
    else:
        pool = [m.id for m in rotation._models]
        if model_count:
            pool = pool[:model_count]
        models = pool

    if not models:
        return {"error": "No models available. Set OPENROUTER_API_KEY and refresh."}

    # Select tasks
    tasks = _select_tasks(task_ids, categories)
    if not tasks:
        return {"error": f"No valid tasks. Available categories: {sorted(CATEGORIES)}. Task IDs: {list(BENCHMARK_TASKS.keys())[:10]}..."}

    repeats = max(1, min(repeats, 5))  # clamp 1-5
    total_work = len(models) * len(tasks) * repeats
    run_state = {
        "run_id": run_id,
        "status": "running",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "models": models,
        "task_ids": [t.task_id for t in tasks],
        "categories": sorted({t.category for t in tasks}),
        "repeats": repeats,
        "total_work": total_work,
        "completed": 0,
        "current": "",
        "results": {},       # model_id -> ModelScore dict
        "ranking": [],       # sorted by avg_score desc
        "completed_at": None,
        "error": None,
    }
    _benchmark_runs[run_id] = run_state

    # Launch background runner
    asyncio.create_task(_run_benchmark_loop(run_id, models, tasks, repeats))

    return {
        "run_id": run_id,
        "status": "running",
        "models": len(models),
        "tasks": len(tasks),
        "repeats": repeats,
        "categories": run_state["categories"],
        "total_work": total_work,
    }


async def _run_benchmark_loop(
    run_id: str,
    models: list[str],
    tasks: list[BenchmarkTask],
    repeats: int = 1,
) -> None:
    """Background loop: run each model×task×repeat combination sequentially."""
    from ..integrations.nemoclaw import NemoClawAgent, NemotronClient

    run = _benchmark_runs[run_id]
    all_results: dict[str, list[TaskResult]] = {m: [] for m in models}

    for model_id in models:
        for task in tasks:
            for rep in range(repeats):
                rep_label = f" (run {rep+1}/{repeats})" if repeats > 1 else ""
                run["current"] = f"{model_id} / {task.task_id}{rep_label}"
                logger.info("Benchmark [%s] running %s on %s%s", run_id, task.task_id, model_id, rep_label)

                # Create agent pinned to this specific model
                nemotron = NemotronClient(model=model_id)
                agent = NemoClawAgent(nemotron=nemotron, max_turns=task.max_turns)

                # Wire internal dispatch
                try:
                    from ..api.mcp_server import _dispatch
                    agent.bridge.set_internal_dispatch(_dispatch)
                except ImportError:
                    pass

                # Run with timeout
                try:
                    result_data = await asyncio.wait_for(
                        agent.run_instrumented(task.prompt),
                        timeout=task.timeout_s,
                    )
                except asyncio.TimeoutError:
                    result_data = {
                        "response": "",
                        "tools_called": [],
                        "turns": 0,
                        "latency_ms": task.timeout_s * 1000,
                        "total_tokens": 0,
                        "tokens_per_sec": 0,
                        "model": model_id,
                        "error": "timeout",
                    }
                except Exception as exc:
                    result_data = {
                        "response": "",
                        "tools_called": [],
                        "turns": 0,
                        "latency_ms": 0,
                        "total_tokens": 0,
                        "tokens_per_sec": 0,
                        "model": model_id,
                        "error": str(exc)[:500],
                    }

                task_result = TaskResult(
                    task_id=task.task_id,
                    model_id=model_id,
                    response=result_data.get("response", ""),
                    tools_called=result_data.get("tools_called", []),
                    turns=result_data.get("turns", 0),
                    latency_ms=result_data.get("latency_ms", 0),
                    total_tokens=result_data.get("total_tokens", 0),
                    tokens_per_sec=result_data.get("tokens_per_sec", 0),
                    error=result_data.get("error"),
                )
                task_result.compute_scores(task)
                all_results[model_id].append(task_result)

                run["completed"] += 1
                logger.info(
                    "Benchmark [%s] %s/%s%s → score=%.3f tools=%s latency=%.0fms",
                    run_id, model_id.split("/")[-1], task.task_id, rep_label,
                    task_result.score, task_result.tools_called, task_result.latency_ms,
                )

    # Aggregate
    import statistics

    model_scores = []
    for model_id in models:
        ms = ModelScore.from_results(model_id, all_results[model_id])
        model_scores.append(ms)

    # Rank by avg_score desc, then by latency asc
    model_scores.sort(key=lambda m: (-m.avg_score, m.avg_latency_ms))

    ranking = []
    for i, ms in enumerate(model_scores):
        scores = [r.score for r in all_results[ms.model_id]]
        entry = {
            "rank": i + 1,
            "model": ms.model_id,
            "avg_score": ms.avg_score,
            "tool_accuracy": ms.avg_tool_accuracy,
            "keyword_hit": ms.avg_keyword_hit,
            "completion": ms.completion_rate,
            "avg_latency_ms": ms.avg_latency_ms,
            "tokens_per_sec": ms.avg_tokens_per_sec,
            "errors": ms.total_errors,
            "category_scores": ms.category_scores,
        }
        # Add variance stats when repeats > 1
        if repeats > 1 and len(scores) > 1:
            entry["score_stddev"] = round(statistics.stdev(scores), 3)
            entry["score_min"] = round(min(scores), 3)
            entry["score_max"] = round(max(scores), 3)
        ranking.append(entry)

    run["results"] = {ms.model_id: ms.to_dict() for ms in model_scores}
    run["ranking"] = ranking
    run["status"] = "complete"
    run["completed_at"] = datetime.now(timezone.utc).isoformat()
    run["current"] = ""

    # Persist to disk
    _persist_run(run)

    logger.info(
        "Benchmark [%s] COMPLETE — %d models × %d tasks × %d repeats. Winner: %s (%.3f)",
        run_id, len(models), len(tasks), repeats,
        ranking[0]["model"] if ranking else "none",
        ranking[0]["avg_score"] if ranking else 0,
    )


def _persist_run(run: dict) -> None:
    """Save benchmark run to disk."""
    runs_dir = Path(__file__).parent.parent.parent / "data" / "benchmark_runs"
    out_dir = runs_dir / f"model-bench-{run['run_id']}"
    out_dir.mkdir(parents=True, exist_ok=True)
    with open(out_dir / "results.json", "w") as f:
        json.dump(run, f, indent=2, default=str)


def get_benchmark_run(run_id: str) -> dict | None:
    """Get benchmark run state."""
    return _benchmark_runs.get(run_id)


def list_benchmark_runs() -> list[dict]:
    """List all benchmark runs (summary only)."""
    return [
        {
            "run_id": r["run_id"],
            "status": r["status"],
            "started_at": r["started_at"],
            "completed_at": r.get("completed_at"),
            "models": len(r["models"]),
            "tasks": len(r["task_ids"]),
            "completed": r["completed"],
            "total_work": r["total_work"],
        }
        for r in _benchmark_runs.values()
    ]
