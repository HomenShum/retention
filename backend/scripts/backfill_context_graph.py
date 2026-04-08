#!/usr/bin/env python3
"""
Backfill ContextGraph from all existing retention.sh data sources.

Usage:
    cd backend && python3 scripts/backfill_context_graph.py
    cd backend && python3 scripts/backfill_context_graph.py --sources pipeline_results,run_logs
    cd backend && python3 scripts/backfill_context_graph.py --dry-run

Sources:
    pipeline_results  — data/pipeline_results/*.json
    run_logs          — data/run_logs/*.json
    exploration_memory — data/exploration_memory/
    handoff           — data/handoff/*.md
    golden_bugs       — data/golden_bugs.json
    git_history       — git log since 2026-03-09
    slack_history     — Slack API (requires SLACK_BOT_TOKEN)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# PYTHONPATH setup so we can import from app.*
# ---------------------------------------------------------------------------
_BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND_DIR))

from app.services.context_graph import (
    ActionNode,
    ContextGraph,
    EdgeType,
    ObservationNode,
    OutcomeNode,
    PipelineHooks,
    SlackAgentHooks,
    StateNode,
    TaskNode,
    VerdictNode,
)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
DATA_DIR = _BACKEND_DIR / "data"
PIPELINE_RESULTS_DIR = DATA_DIR / "pipeline_results"
RUN_LOGS_DIR = DATA_DIR / "run_logs"
EXPLORATION_MEMORY_DIR = DATA_DIR / "exploration_memory"
HANDOFF_DIR = DATA_DIR / "handoff"
GOLDEN_BUGS_FILE = DATA_DIR / "golden_bugs.json"
OUTPUT_DIR = DATA_DIR / "context_graphs"

ALL_SOURCES = [
    "pipeline_results",
    "run_logs",
    "exploration_memory",
    "handoff",
    "golden_bugs",
    "git_history",
    "slack_history",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_json(path: Path) -> Optional[Any]:
    """Load a JSON file, returning None on failure."""
    try:
        with open(path, "r") as f:
            return json.load(f)
    except Exception as e:
        print(f"  WARN: Could not load {path.name}: {e}")
        return None


def _pass_rate_to_verdict(pass_rate: float) -> str:
    """Map pass_rate to a VerdictType string."""
    if pass_rate >= 0.95:
        return "correct"
    elif pass_rate >= 0.5:
        return "app_bug"
    else:
        return "app_bug"


def _status_str_to_outcome(status: str) -> str:
    """Normalize status strings to OutcomeStatus literals."""
    s = status.lower().strip()
    mapping = {
        "pass": "success",
        "passed": "success",
        "success": "success",
        "fail": "failure",
        "failed": "failure",
        "failure": "failure",
        "skip": "blocked",
        "skipped": "blocked",
        "blocked": "blocked",
        "flaky": "flaky",
        "timeout": "timeout",
        "error": "failure",
    }
    return mapping.get(s, "failure")


# ---------------------------------------------------------------------------
# Source: Pipeline Results
# ---------------------------------------------------------------------------

def backfill_pipeline_results(graph: ContextGraph, dry_run: bool) -> int:
    """Backfill from data/pipeline_results/*.json."""
    if not PIPELINE_RESULTS_DIR.exists():
        print("  Directory not found: data/pipeline_results/")
        return 0

    files = sorted(PIPELINE_RESULTS_DIR.glob("*.json"))
    if dry_run:
        print(f"  Would import {len(files)} pipeline result files")
        return len(files)

    count = 0
    for fpath in files:
        data = _load_json(fpath)
        if data is None:
            continue

        run_id = data.get("run_id", fpath.stem)
        app_name = data.get("app_name", "")
        duration_s = data.get("duration_s", 0)

        # Create TaskNode for the run
        task = TaskNode(
            run_id=run_id,
            intent=f"QA pipeline: {app_name}" if app_name else f"QA pipeline run {run_id}",
            goal_state="all tests pass",
            source="qa_pipeline",
            metadata={
                "app_name": app_name,
                "duration_s": duration_s,
                "flow_type": data.get("flow_type", ""),
                "file": fpath.name,
            },
        )
        if data.get("started_at"):
            task.created_at = data["started_at"]
        graph.add_node(task)

        # Process test case execution results
        result_block = data.get("result", {})
        execution = result_block.get("execution", {})

        # execution can be a dict with "results" key, or a list directly
        if isinstance(execution, list):
            results_list = execution
        elif isinstance(execution, dict):
            results_list = execution.get("results", [])
        else:
            results_list = []

        for tc_result in results_list:
            test_id = tc_result.get("test_id", "")
            status = _status_str_to_outcome(tc_result.get("status", "failure"))

            outcome = OutcomeNode(
                run_id=run_id,
                status=status,
                test_id=test_id,
                evidence={
                    "name": tc_result.get("name", ""),
                    "priority": tc_result.get("priority", ""),
                    "category": tc_result.get("category", ""),
                    "steps_executed": tc_result.get("steps_executed", 0),
                    "steps_total": tc_result.get("steps_total", 0),
                },
            )
            graph.add_node(outcome)
            graph.add_edge(task.id, outcome.id, EdgeType.ACTION_EXPECTED_RESULT)

        # Create VerdictNode from pass_rate
        if isinstance(execution, dict):
            pass_rate = execution.get("pass_rate", result_block.get("pass_rate"))
            passed = execution.get("passed", 0)
            failed = execution.get("failed", 0)
        else:
            pass_rate = result_block.get("pass_rate")
            passed = sum(1 for r in results_list if _status_str_to_outcome(r.get("status", "")) == "success")
            failed = len(results_list) - passed

        if pass_rate is not None:
            verdict = VerdictNode(
                run_id=run_id,
                verdict_type=_pass_rate_to_verdict(pass_rate),
                confidence=pass_rate,
                reasoning=f"pass_rate={pass_rate} ({passed}p/{failed}f)",
                metadata={"pass_rate": pass_rate, "passed": passed, "failed": failed},
            )
            graph.add_node(verdict)
            graph.add_edge(task.id, verdict.id, EdgeType.OUTCOME_JUDGED_AS)

        count += 1
        if count % 20 == 0:
            print(f"  ... processed {count}/{len(files)} pipeline results")

    print(f"  Imported {count} pipeline results")
    return count


# ---------------------------------------------------------------------------
# Source: Run Logs
# ---------------------------------------------------------------------------

def backfill_run_logs(graph: ContextGraph, dry_run: bool) -> int:
    """Backfill from data/run_logs/*.json."""
    if not RUN_LOGS_DIR.exists():
        print("  Directory not found: data/run_logs/")
        return 0

    files = sorted(RUN_LOGS_DIR.glob("*.json"))
    if dry_run:
        print(f"  Would import {len(files)} run log files")
        return len(files)

    count = 0
    for fpath in files:
        data = _load_json(fpath)
        if data is None:
            continue

        run_id = data.get("run_id", fpath.stem)
        app_name = data.get("app_name", "")

        # Skip if we already have a task for this run_id (from pipeline_results)
        existing = graph.nodes_by_run(run_id)
        task_node = None
        for n in existing:
            if isinstance(n, TaskNode):
                task_node = n
                break

        if task_node is None:
            task_node = TaskNode(
                run_id=run_id,
                intent=f"QA run log: {app_name}" if app_name else f"QA run log {run_id}",
                goal_state="all tests pass",
                source="qa_pipeline",
                metadata={
                    "app_name": app_name,
                    "duration_s": data.get("duration_s", 0),
                    "flow_type": data.get("flow_type", ""),
                    "file": fpath.name,
                },
            )
            if data.get("timestamp"):
                task_node.created_at = data["timestamp"]
            graph.add_node(task_node)

        # Extract compact bundle failures
        bundle = data.get("compact_bundle", {})
        summary = bundle.get("summary", {})
        failures = bundle.get("failures", [])

        for failure in failures:
            test_id = failure.get("test_id", "")
            verdict = VerdictNode(
                run_id=run_id,
                verdict_type="app_bug",
                confidence=0.8,
                reasoning=failure.get("reason", failure.get("failure_reason", "")),
                metadata={
                    "test_id": test_id,
                    "name": failure.get("name", ""),
                    "suggested_fix": failure.get("suggested_fix", ""),
                },
            )
            graph.add_node(verdict)
            graph.add_edge(task_node.id, verdict.id, EdgeType.OUTCOME_JUDGED_AS)

        # If no failures processed but we have summary, add a pass verdict
        if not failures and summary.get("pass_rate") is not None:
            pass_rate = summary["pass_rate"]
            verdict = VerdictNode(
                run_id=run_id,
                verdict_type=_pass_rate_to_verdict(pass_rate),
                confidence=pass_rate,
                reasoning=f"run_log pass_rate={pass_rate}",
                metadata={"summary": summary},
            )
            graph.add_node(verdict)
            graph.add_edge(task_node.id, verdict.id, EdgeType.OUTCOME_JUDGED_AS)

        count += 1
        if count % 20 == 0:
            print(f"  ... processed {count}/{len(files)} run logs")

    print(f"  Imported {count} run logs")
    return count


# ---------------------------------------------------------------------------
# Source: Exploration Memory
# ---------------------------------------------------------------------------

def backfill_exploration_memory(graph: ContextGraph, dry_run: bool) -> int:
    """Backfill from data/exploration_memory/."""
    if not EXPLORATION_MEMORY_DIR.exists():
        print("  Directory not found: data/exploration_memory/")
        return 0

    index_path = EXPLORATION_MEMORY_DIR / "memory_index.json"
    crawl_dir = EXPLORATION_MEMORY_DIR / "crawl"
    workflow_dir = EXPLORATION_MEMORY_DIR / "workflows"
    suite_dir = EXPLORATION_MEMORY_DIR / "test_suites"

    node_count = 0

    # -- Memory index --
    index_data = _load_json(index_path) if index_path.exists() else None
    apps = (index_data or {}).get("apps", {})

    if dry_run:
        crawl_files = list(crawl_dir.glob("*.json")) if crawl_dir.exists() else []
        wf_files = list(workflow_dir.glob("*.json")) if workflow_dir.exists() else []
        suite_files = list(suite_dir.glob("*.json")) if suite_dir.exists() else []
        total = len(apps) + len(crawl_files) + len(wf_files) + len(suite_files)
        print(f"  Would import: {len(apps)} app entries, {len(crawl_files)} crawls, "
              f"{len(wf_files)} workflow files, {len(suite_files)} test suite files")
        return total

    # -- Crawl data: screens become StateNodes --
    if crawl_dir.exists():
        for fpath in sorted(crawl_dir.glob("*.json")):
            data = _load_json(fpath)
            if data is None:
                continue

            app_key = data.get("app_key", fpath.stem)
            app_name = data.get("app_name", "")
            crawl_data = data.get("crawl_data", {})
            screens = crawl_data.get("screens", [])

            for screen in screens:
                screen_id = screen.get("screen_id", "")
                components = [
                    c.get("element_type", "")
                    for c in screen.get("components", [])
                    if c.get("element_type")
                ]
                state = StateNode(
                    run_id=f"crawl-{app_key}",
                    app_state=f"screen:{screen.get('screen_name', screen_id)}",
                    screen_hash=data.get("crawl_fingerprint", ""),
                    components=components,
                    metadata={
                        "app_name": app_name,
                        "screen_id": screen_id,
                        "screen_name": screen.get("screen_name", ""),
                        "navigation_depth": screen.get("navigation_depth", 0),
                    },
                )
                if data.get("stored_at"):
                    state.created_at = data["stored_at"]
                graph.add_node(state)
                node_count += 1

    # -- Workflow data: each workflow becomes a TaskNode --
    if workflow_dir.exists():
        for fpath in sorted(workflow_dir.glob("*.json")):
            data = _load_json(fpath)
            if data is None:
                continue

            app_key = data.get("app_key", "")
            wf_raw = data.get("workflow_data", "")
            if isinstance(wf_raw, str):
                try:
                    wf_parsed = json.loads(wf_raw)
                except Exception:
                    wf_parsed = {}
            else:
                wf_parsed = wf_raw

            workflows = wf_parsed.get("workflows", [])
            wf_app_name = wf_parsed.get("app_name", "")

            for wf in workflows:
                wf_id = wf.get("workflow_id", "")
                task = TaskNode(
                    run_id=f"workflow-{app_key}",
                    intent=f"workflow: {wf.get('name', wf_id)}",
                    goal_state=wf.get("description", ""),
                    source="qa_pipeline",
                    metadata={
                        "app_name": wf_app_name,
                        "workflow_id": wf_id,
                        "complexity": wf.get("complexity", ""),
                        "steps": len(wf.get("steps", [])),
                    },
                )
                if data.get("stored_at"):
                    task.created_at = data["stored_at"]
                graph.add_node(task)
                node_count += 1

    # -- Test suites --
    if suite_dir.exists():
        for fpath in sorted(suite_dir.glob("*.json")):
            data = _load_json(fpath)
            if data is None:
                continue

            app_key = data.get("app_key", "")
            suite_raw = data.get("test_suite_data", "")
            if isinstance(suite_raw, str):
                try:
                    suite_parsed = json.loads(suite_raw)
                except Exception:
                    suite_parsed = {}
            else:
                suite_parsed = suite_raw

            test_cases = suite_parsed.get("test_cases", [])
            for tc in test_cases:
                tc_id = tc.get("test_id", "")
                task = TaskNode(
                    run_id=f"suite-{app_key}",
                    intent=f"test: {tc.get('name', tc_id)}",
                    goal_state=tc.get("expected_result", ""),
                    source="qa_pipeline",
                    metadata={
                        "test_id": tc_id,
                        "priority": tc.get("priority", ""),
                        "category": tc.get("category", ""),
                        "workflow_id": tc.get("workflow_id", ""),
                    },
                )
                if data.get("stored_at"):
                    task.created_at = data["stored_at"]
                graph.add_node(task)
                node_count += 1

    print(f"  Imported {node_count} nodes from exploration memory")
    return node_count


# ---------------------------------------------------------------------------
# Source: Handoff Docs
# ---------------------------------------------------------------------------

def backfill_handoff(graph: ContextGraph, dry_run: bool) -> int:
    """Backfill from data/handoff/*.md."""
    if not HANDOFF_DIR.exists():
        print("  Directory not found: data/handoff/")
        return 0

    files = sorted(HANDOFF_DIR.glob("*.md"))
    if dry_run:
        print(f"  Would import {len(files)} handoff docs")
        return len(files)

    count = 0
    for fpath in files:
        try:
            content = fpath.read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            print(f"  WARN: Could not read {fpath.name}: {e}")
            continue

        run_id = fpath.stem

        # Extract verdict from markdown
        verdict_type = "inconclusive"
        confidence = 0.5
        reasoning = ""

        # Look for **Verdict:** PASS/FAIL
        verdict_match = re.search(r"\*\*Verdict:\*\*\s*(\w+)", content)
        if verdict_match:
            v = verdict_match.group(1).upper()
            if v == "PASS":
                verdict_type = "correct"
                confidence = 1.0
            elif v == "FAIL":
                verdict_type = "app_bug"
                confidence = 0.8
            elif v == "BLOCKED":
                verdict_type = "environment"
                confidence = 0.6

        # Extract pass rate
        rate_match = re.search(r"\*\*Pass Rate:\*\*\s*([\d.]+)%", content)
        if rate_match:
            pass_rate = float(rate_match.group(1)) / 100.0
            confidence = pass_rate
            reasoning = f"pass_rate={pass_rate}"

        # Extract app name
        app_match = re.search(r"\*\*App:\*\*\s*(.+)", content)
        app_name = app_match.group(1).strip() if app_match else ""

        verdict = VerdictNode(
            run_id=run_id,
            verdict_type=verdict_type,
            judge_model="handoff_report",
            confidence=confidence,
            reasoning=reasoning,
            metadata={
                "app_name": app_name,
                "file": fpath.name,
            },
        )
        graph.add_node(verdict)
        count += 1

        # Link to existing task node for the same run_id if present
        for n in graph.nodes_by_run(run_id):
            if isinstance(n, TaskNode):
                graph.add_edge(n.id, verdict.id, EdgeType.OUTCOME_JUDGED_AS)
                break

    print(f"  Imported {count} handoff verdicts")
    return count


# ---------------------------------------------------------------------------
# Source: Golden Bugs
# ---------------------------------------------------------------------------

def backfill_golden_bugs(graph: ContextGraph, dry_run: bool) -> int:
    """Backfill from data/golden_bugs.json."""
    if not GOLDEN_BUGS_FILE.exists():
        print("  File not found: data/golden_bugs.json")
        return 0

    data = _load_json(GOLDEN_BUGS_FILE)
    if data is None:
        return 0

    bugs = data if isinstance(data, list) else []
    if dry_run:
        print(f"  Would import {len(bugs)} golden bugs")
        return len(bugs)

    count = 0
    for bug in bugs:
        bug_id = bug.get("bug_id", "")
        report = bug.get("bug_report", {})

        task = TaskNode(
            run_id=f"golden-{bug_id}",
            intent=f"golden bug: {bug.get('name', bug_id)}",
            goal_state=report.get("expected_behavior", ""),
            source="qa_pipeline",
            metadata={
                "bug_id": bug_id,
                "description": bug.get("description", ""),
                "severity": report.get("severity", ""),
                "app_package": report.get("app_package", ""),
                "tags": report.get("tags", []),
            },
        )
        graph.add_node(task)

        # Expected outcome
        expected = bug.get("auto_check", {}).get("expectation", "reproduced")
        outcome = OutcomeNode(
            run_id=f"golden-{bug_id}",
            status="failure" if expected == "reproduced" else "success",
            test_id=bug_id,
            evidence={
                "expected_behavior": report.get("expected_behavior", ""),
                "actual_behavior": report.get("actual_behavior", ""),
                "reproduction_steps": report.get("reproduction_steps", []),
            },
        )
        graph.add_node(outcome)
        graph.add_edge(task.id, outcome.id, EdgeType.ACTION_EXPECTED_RESULT)
        count += 1

    print(f"  Imported {count} golden bugs")
    return count


# ---------------------------------------------------------------------------
# Source: Git History
# ---------------------------------------------------------------------------

def backfill_git_history(graph: ContextGraph, dry_run: bool) -> int:
    """Backfill from git log since 2026-03-09."""
    try:
        result = subprocess.run(
            ["git", "log", "--oneline", "--since=2026-03-09", "--format=%H|%s|%aI"],
            capture_output=True,
            text=True,
            cwd=str(_BACKEND_DIR.parent),  # repo root
            timeout=30,
        )
        if result.returncode != 0:
            print(f"  WARN: git log failed: {result.stderr.strip()}")
            return 0
    except Exception as e:
        print(f"  WARN: Could not run git log: {e}")
        return 0

    lines = [l.strip() for l in result.stdout.strip().split("\n") if l.strip()]

    if dry_run:
        print(f"  Would import {len(lines)} git commits")
        return len(lines)

    count = 0
    for line in lines:
        parts = line.split("|", 2)
        if len(parts) < 3:
            continue

        commit_hash, message, date_iso = parts[0], parts[1], parts[2]

        # Determine action type from conventional commit prefix
        action_type: str = "api_call"  # default for misc commits
        msg_lower = message.lower()
        if msg_lower.startswith("feat"):
            action_type = "tool_call"
        elif msg_lower.startswith("fix"):
            action_type = "tool_call"
        elif msg_lower.startswith("chore"):
            action_type = "api_call"
        elif msg_lower.startswith("docs"):
            action_type = "api_call"
        elif msg_lower.startswith("refactor"):
            action_type = "tool_call"

        node = ActionNode(
            run_id=f"git-{commit_hash[:8]}",
            action_type=action_type,
            target=f"commit:{commit_hash[:8]}",
            result=message,
            metadata={
                "commit_hash": commit_hash,
                "message": message,
                "date": date_iso,
            },
        )
        node.created_at = date_iso
        graph.add_node(node)
        count += 1

    print(f"  Imported {count} git commits")
    return count


# ---------------------------------------------------------------------------
# Source: Slack History
# ---------------------------------------------------------------------------

def backfill_slack_history(graph: ContextGraph, dry_run: bool) -> int:
    """Backfill from Slack API if SLACK_BOT_TOKEN is available."""
    token = os.environ.get("SLACK_BOT_TOKEN")
    if not token:
        print("  SLACK_BOT_TOKEN not set — skipping Slack history")
        return 0

    channel = os.environ.get("CLAW_CHANNEL", "")
    if not channel:
        print("  CLAW_CHANNEL not set — skipping Slack history")
        return 0

    try:
        import urllib.request
        import urllib.parse
    except ImportError:
        print("  WARN: urllib not available")
        return 0

    if dry_run:
        print(f"  Would fetch up to 200 messages from Slack channel {channel}")
        return 0  # Can't know exact count without API call

    # Fetch messages via Slack Web API
    url = "https://slack.com/api/conversations.history"
    params = urllib.parse.urlencode({"channel": channel, "limit": 200})
    req = urllib.request.Request(
        f"{url}?{params}",
        headers={"Authorization": f"Bearer {token}"},
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        print(f"  WARN: Slack API call failed: {e}")
        return 0

    if not data.get("ok"):
        print(f"  WARN: Slack API error: {data.get('error', 'unknown')}")
        return 0

    messages = data.get("messages", [])
    hooks = SlackAgentHooks(graph)
    count = 0

    for msg in messages:
        ts = msg.get("ts", "")
        user = msg.get("user", msg.get("bot_id", "unknown"))
        text = msg.get("text", "")
        files = msg.get("files", [])
        subtype = msg.get("subtype", "")

        if not text:
            continue

        # Determine if this is a user mention (task) or bot reply
        is_bot = msg.get("bot_id") or subtype == "bot_message"

        if is_bot:
            # Bot reply -> ActionNode
            node = ActionNode(
                run_id=f"slack-{channel}-{ts}",
                action_type="slack_post",
                target=f"channel:{channel}",
                result=text[:256],
                metadata={
                    "ts": ts,
                    "bot_id": msg.get("bot_id", ""),
                    "thread_ts": msg.get("thread_ts", ""),
                },
            )
            graph.add_node(node)

            # Link to parent thread task if exists
            thread_ts = msg.get("thread_ts", "")
            if thread_ts and thread_ts != ts:
                parent_run_id = f"slack-{channel}-{thread_ts}"
                for n in graph.nodes_by_run(parent_run_id):
                    if isinstance(n, TaskNode):
                        graph.add_edge(n.id, node.id, EdgeType.ACTION_TAKEN_FROM_STATE)
                        break
        else:
            # User message -> TaskNode
            file_dicts = [
                {"name": f.get("name", ""), "url_private": f.get("url_private", "")}
                for f in files
            ]
            task = hooks.on_message_received(
                channel=channel,
                ts=ts,
                user=user,
                text=text,
                files=file_dicts if file_dicts else None,
            )

            # File attachments -> ObservationNodes (already handled by hooks)

        count += 1

    print(f"  Imported {count} Slack messages")
    return count


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

SOURCE_HANDLERS = {
    "pipeline_results": backfill_pipeline_results,
    "run_logs": backfill_run_logs,
    "exploration_memory": backfill_exploration_memory,
    "handoff": backfill_handoff,
    "golden_bugs": backfill_golden_bugs,
    "git_history": backfill_git_history,
    "slack_history": backfill_slack_history,
}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Backfill ContextGraph from existing retention.sh data sources."
    )
    parser.add_argument(
        "--sources",
        type=str,
        default=",".join(ALL_SOURCES),
        help=f"Comma-separated list of sources to backfill (default: all). "
             f"Available: {', '.join(ALL_SOURCES)}",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Count what would be imported without actually doing it.",
    )
    args = parser.parse_args()

    sources = [s.strip() for s in args.sources.split(",") if s.strip()]
    invalid = [s for s in sources if s not in SOURCE_HANDLERS]
    if invalid:
        parser.error(f"Unknown sources: {', '.join(invalid)}. "
                      f"Available: {', '.join(ALL_SOURCES)}")

    print("=" * 60)
    print("retention.sh Context Graph Backfill")
    print(f"  Sources: {', '.join(sources)}")
    print(f"  Dry run: {args.dry_run}")
    print("=" * 60)

    graph = ContextGraph()
    totals: Dict[str, int] = {}

    for source in sources:
        print(f"\n[{source}]")
        handler = SOURCE_HANDLERS[source]
        totals[source] = handler(graph, args.dry_run)

    # Summary
    print("\n" + "=" * 60)
    print("Summary")
    print("=" * 60)

    total_items = sum(totals.values())
    for source, count in totals.items():
        print(f"  {source:25s} {count:>6d} items")
    print(f"  {'TOTAL':25s} {total_items:>6d} items")

    if not args.dry_run:
        node_counts: Dict[str, int] = {}
        for n in graph._nodes.values():
            kind = n.kind.value
            node_counts[kind] = node_counts.get(kind, 0) + 1

        print(f"\nGraph: {len(graph._nodes)} nodes, {len(graph._edges)} edges")
        for kind, cnt in sorted(node_counts.items()):
            print(f"  {kind:20s} {cnt:>6d}")

        # Save
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        output_path = OUTPUT_DIR / f"backfill_{timestamp}.json"
        graph.save(output_path)
        print(f"\nSaved to: {output_path}")

        # Compute and display metrics
        metrics = graph.compute_metrics()
        print(f"\nMetrics:")
        print(f"  state_recognition_accuracy:  {metrics.state_recognition_accuracy}")
        print(f"  action_appropriateness:      {metrics.action_appropriateness}")
        print(f"  hypothesis_validation_rate:  {metrics.hypothesis_validation_rate}")
        print(f"  precedent_reuse_lift:        {metrics.precedent_reuse_lift}")
        print(f"  bug_attribution_accuracy:    {metrics.bug_attribution_accuracy}")
        print(f"  recovery_success_rate:       {metrics.recovery_success_rate}")
    else:
        print(f"\n(Dry run — no graph created)")


if __name__ == "__main__":
    main()
