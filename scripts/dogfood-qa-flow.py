#!/usr/bin/env python3
"""Dogfood script — simulates the full retention.sh QA journey.

Walks through every step a user's Claude Code / OpenClaw agent would take when
connecting to retention.sh, running QA, collecting a verdict, and exercising the
failure-analysis + rerun loop.

Usage:
    python scripts/dogfood-qa-flow.py [--url URL] [--backend URL] [--token TOKEN]

Requires: requests, python 3.10+
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime
from typing import Any

try:
    import requests
except ImportError:
    print("pip install requests")
    sys.exit(1)


DEFAULT_SIGNUP_EMAIL = (
    os.environ.get("TA_DOGFOOD_EMAIL")
    or os.environ.get("RETENTION_EMAIL")
    or "homen@retention.com"
)
DEFAULT_SIGNUP_NAME = (
    os.environ.get("TA_DOGFOOD_NAME")
    or os.environ.get("RETENTION_NAME")
    or "Homen"
)
DEFAULT_PLATFORM = os.environ.get("TA_DOGFOOD_PLATFORM") or "claude-code"
DEFAULT_APP_NAME = os.environ.get("TA_DOGFOOD_APP_NAME") or "Transformers Repair Service"


def log(step: str, msg: str, data: dict | None = None):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] STEP {step}: {msg}")
    if data:
        for k, v in data.items():
            val = json.dumps(v, default=str) if isinstance(v, (dict, list)) else str(v)
            if len(val) > 200:
                val = val[:200] + "..."
            print(f"         {k}: {val}")
    print()


def call_mcp(backend: str, token: str, tool: str, args: dict) -> dict:
    """Call an MCP tool via the HTTP tool-call endpoint and unwrap the result."""
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    try:
        resp = requests.post(
            f"{backend}/mcp/tools/call",
            headers=headers,
            json={"tool": tool, "arguments": args},
            timeout=600,
        )
    except Exception as exc:
        return {"error": f"Request failed: {exc}"}

    if resp.status_code != 200:
        return {"error": f"HTTP {resp.status_code}: {resp.text[:300]}"}

    try:
        payload = resp.json()
    except ValueError:
        return {"error": f"Invalid JSON response: {resp.text[:300]}"}

    if payload.get("status") != "ok":
        return {"error": payload.get("error") or f"MCP call failed for {tool}"}

    result = payload.get("result")
    if result is None:
        return {}
    if isinstance(result, dict):
        return result
    return {"value": result}


def poll_pipeline(backend: str, token: str, run_id: str, timeout: int = 600) -> dict:
    """Poll retention.pipeline.status until complete or timeout."""
    start = time.time()
    while time.time() - start < timeout:
        result = call_mcp(backend, token, "retention.pipeline.status", {"run_id": run_id})
        if result.get("error"):
            return result

        status = result.get("status", "unknown")
        if status in ("complete", "completed", "error"):
            return result

        stage = result.get("current_stage", "?")
        progress = result.get("progress", {})
        print(f"         ... [{stage}] screens={progress.get('screens', '?')}", end="\r")
        time.sleep(5)
    return {"error": f"Timed out after {timeout}s"}


def signup_for_token(
    backend: str,
    email: str,
    name: str,
    *,
    platform: str = DEFAULT_PLATFORM,
    timeout: int = 30,
) -> dict:
    """Sign up for a TA MCP token using the configured identity."""
    try:
        resp = requests.post(
            f"{backend}/api/signup",
            json={"email": email, "name": name, "platform": platform},
            timeout=timeout,
        )
    except Exception as exc:
        return {"error": f"Signup request failed: {exc}"}

    if resp.status_code != 200:
        return {"error": f"Signup failed: HTTP {resp.status_code}: {resp.text[:300]}"}

    try:
        return resp.json()
    except ValueError:
        return {"error": f"Signup returned invalid JSON: {resp.text[:300]}"}


def extract_failure_count(bundle: dict[str, Any]) -> int:
    """Best-effort failure count across failure bundle / verdict response shapes."""
    failures = bundle.get("failures")
    if isinstance(failures, list):
        return len(failures)

    summary = bundle.get("summary")
    if isinstance(summary, dict):
        for key in ("failed", "failures", "failed_tests"):
            value = summary.get(key)
            if value is not None:
                try:
                    return int(value)
                except (TypeError, ValueError):
                    pass

    for key in ("failure_count", "failed", "failed_tests"):
        value = bundle.get(key)
        if value is not None:
            try:
                return int(value)
            except (TypeError, ValueError):
                pass

    return 0


def run_failure_debug_loop(
    backend: str,
    token: str,
    baseline_run_id: str,
    app_url: str,
    *,
    rerun_timeout: int = 600,
    perform_rerun: bool = True,
) -> dict[str, Any]:
    """Exercise the full judge/research/debug loop for a completed QA run.

    This intentionally goes beyond a single verdict:
      1. retention.pipeline.failure_bundle
      2. retention.suggest_fix_context
      3. ta.feedback_package
      4. retention.pipeline.rerun_failures
      5. retention.compare_before_after

    Returns a structured dict so callers can log or assert each stage.
    """
    result: dict[str, Any] = {
        "baseline_run_id": baseline_run_id,
        "status": "started",
    }

    bundle = call_mcp(backend, token, "retention.pipeline.failure_bundle", {"run_id": baseline_run_id})
    result["bundle"] = bundle
    if bundle.get("error"):
        result["status"] = "error"
        result["stage"] = "failure_bundle"
        result["error"] = bundle["error"]
        return result

    failure_count = extract_failure_count(bundle)
    result["failure_count"] = failure_count
    if failure_count <= 0:
        result["status"] = "no_failures"
        return result

    fix_context = call_mcp(backend, token, "retention.suggest_fix_context", {"run_id": baseline_run_id})
    result["fix_context"] = fix_context
    if fix_context.get("error"):
        result["status"] = "error"
        result["stage"] = "suggest_fix_context"
        result["error"] = fix_context["error"]
        return result

    feedback_args = {"run_id": baseline_run_id}
    if app_url:
        feedback_args["app_url"] = app_url
    feedback = call_mcp(backend, token, "ta.feedback_package", feedback_args)
    result["feedback"] = feedback
    if feedback.get("error"):
        result["status"] = "error"
        result["stage"] = "feedback_package"
        result["error"] = feedback["error"]
        return result

    if not perform_rerun:
        result["status"] = "analysis_only"
        return result

    rerun_args = {"baseline_run_id": baseline_run_id, "failures_only": True}
    if app_url:
        rerun_args["app_url"] = app_url
    rerun = call_mcp(backend, token, "retention.pipeline.rerun_failures", rerun_args)
    result["rerun"] = rerun
    if rerun.get("error"):
        result["status"] = "error"
        result["stage"] = "rerun_failures"
        result["error"] = rerun["error"]
        return result

    rerun_run_id = rerun.get("run_id", "")
    if not rerun_run_id:
        result["status"] = "rerun_not_started"
        return result

    rerun_status = poll_pipeline(backend, token, rerun_run_id, timeout=rerun_timeout)
    result["rerun_status"] = rerun_status
    if rerun_status.get("error"):
        result["status"] = "error"
        result["stage"] = "poll_rerun"
        result["error"] = rerun_status["error"]
        return result

    comparison = call_mcp(
        backend,
        token,
        "retention.compare_before_after",
        {
            "baseline_run_id": baseline_run_id,
            "current_run_id": rerun_run_id,
            "include_metrics": True,
        },
    )
    result["comparison"] = comparison
    if comparison.get("error"):
        result["status"] = "error"
        result["stage"] = "compare_before_after"
        result["error"] = comparison["error"]
        return result

    result["status"] = "completed"
    return result


def _require_success(step: str, payload: dict[str, Any]) -> None:
    """Exit immediately when a required dogfood stage fails."""
    if payload.get("error"):
        log(step, f"Required stage failed: {payload['error']}")
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="Dogfood the retention.sh QA flow")
    parser.add_argument("--url", default="http://localhost:5173", help="Target app URL to QA")
    parser.add_argument("--backend", default="http://localhost:8000", help="retention.sh backend URL")
    parser.add_argument("--token", default=os.environ.get("RETENTION_MCP_TOKEN", ""), help="MCP auth token")
    parser.add_argument("--skip-signup", action="store_true", help="Skip signup, use provided token")
    parser.add_argument("--signup-email", default=DEFAULT_SIGNUP_EMAIL, help="Email used for /api/signup")
    parser.add_argument("--signup-name", default=DEFAULT_SIGNUP_NAME, help="Name used for /api/signup")
    parser.add_argument("--platform", default=DEFAULT_PLATFORM, help="Platform sent to /api/signup")
    parser.add_argument("--flow-type", choices=["web", "android"], default="web")
    parser.add_argument("--app-package", default="", help="Android app package (for android flow)")
    parser.add_argument("--app-name", default=DEFAULT_APP_NAME, help="Friendly app name for reports")
    parser.add_argument("--benchmark", type=int, default=0, help="Run N consecutive flows for benchmarking")
    parser.add_argument("--mode", choices=["mcp", "agent", "both"], default="both",
                        help="mcp=individual tools, agent=Coordinator, both=run both paths")
    parser.add_argument("--skip-rerun-loop", action="store_true",
                        help="Stop after analysis tools; skip retention.pipeline.rerun_failures + compare")
    parser.add_argument("--pipeline-timeout", type=int, default=600, help="Seconds to wait for each pipeline run")
    parser.add_argument("--rerun-timeout", type=int, default=600, help="Seconds to wait for rerun verification")
    args = parser.parse_args()

    backend = args.backend.rstrip("/")
    token = args.token

    print("=" * 60)
    print("  retention.sh — Dogfood QA Flow")
    print("  Simulating: full QA + judge + research + rerun loop")
    print("=" * 60)
    print()

    # —— Step 1: Health check ————————————————————————————————————————————————
    log("1", "Checking retention.sh backend health")
    try:
        resp = requests.get(f"{backend}/api/health", timeout=10)
        if resp.status_code == 200:
            log("1", "Backend is healthy", resp.json())
        else:
            log("1", f"Backend returned {resp.status_code}", {"body": resp.text[:200]})
            sys.exit(1)
    except Exception as e:
        log("1", f"Backend unreachable: {e}")
        sys.exit(1)

    # —— Step 2: Signup / Get token ————————————————————————————————————————————
    if not args.skip_signup and not token:
        log("2", "Signing up for API token", {
            "email": args.signup_email,
            "name": args.signup_name,
            "platform": args.platform,
        })
        signup = signup_for_token(
            backend,
            args.signup_email,
            args.signup_name,
            platform=args.platform,
        )
        if signup.get("error"):
            log("2", signup["error"])
            sys.exit(1)
        token = signup.get("token", "")
        log("2", "Token received", {
            "token": token[:20] + "..." if token else "",
            "setup": signup.get("setup_snippet", "")[:100],
        })
    else:
        log("2", "Using existing token", {
            "token": (token[:20] + "...") if token else "(none)",
            "signup_email": args.signup_email,
        })

    # —— Step 3: System check ————————————————————————————————————————————————
    log("3", "Running system check (retention.system_check)")
    result = call_mcp(backend, token, "retention.system_check", {})
    _require_success("3", result)
    log("3", "System check result", result)

    # —— Step 4: Connection info ——————————————————————————————————————————————
    log("4", "Getting connection info (ta.meta.connection_info)")
    result = call_mcp(backend, token, "ta.meta.connection_info", {})
    _require_success("4", result)
    log("4", "Connection info", result)

    pipeline_ready = result.get("pipeline_service_ready", False)
    if not pipeline_ready:
        log("4", "NOTE: No emulator connected — web QA will use Playwright (headless browser)")
        log("4", "For Android QA, start an emulator first.")

    # =================================================================
    # PATH A: Agent mode — talk to the TA Coordinator
    # =================================================================
    if args.mode in ("agent", "both"):
        print("\n" + "-" * 60)
        print("  PATH A: Coordinator Agent (ta.agent.run)")
        print("-" * 60 + "\n")

        log("A1", "Sending QA request to TA Coordinator agent")
        target = args.url if args.flow_type == "web" else args.app_package
        agent_msg = f"Run QA on my app at {target} — check all core flows, report bugs"
        agent_result = call_mcp(backend, token, "ta.agent.run", {
            "message": agent_msg,
            "app_url": args.url,
        })

        if agent_result.get("error"):
            log("A1", f"Agent unavailable: {agent_result['error']}")
            log("A1", "This is expected if the Coordinator service isn't configured.")
        else:
            log("A1", "Agent responded", {
                "session_id": agent_result.get("session_id"),
                "tool_calls": agent_result.get("tool_calls_made"),
                "response_length": len(agent_result.get("response", "")),
            })
            resp_text = agent_result.get("response", "")
            if resp_text:
                print("         Agent response (first 500 chars):")
                print(f"         {resp_text[:500]}")
                print()

    # =================================================================
    # PATH B: MCP tools — direct tool calls
    # =================================================================
    if args.mode in ("mcp", "both"):
        print("\n" + "-" * 60)
        print("  PATH B: Direct MCP Tools (run → verdict → debug loop)")
        print("-" * 60 + "\n")

    if args.mode not in ("mcp", "both"):
        print("\n" + "=" * 60)
        print("  Dogfood complete (agent mode only)")
        print("=" * 60)
        return

    # —— Step 5: Run QA flow ————————————————————————————————————————————————
    if args.flow_type == "web":
        log("5", f"Starting web QA flow (retention.run_web_flow) for {args.url}")
        result = call_mcp(backend, token, "retention.run_web_flow", {
            "url": args.url,
            "app_name": args.app_name,
            "timeout_seconds": 300,
        })
    else:
        log("5", f"Starting android QA flow (retention.run_android_flow) for {args.app_package}")
        result = call_mcp(backend, token, "retention.run_android_flow", {
            "app_package": args.app_package,
            "app_name": args.app_name,
            "timeout_seconds": 300,
        })

    if result.get("error"):
        log("5", f"Flow start failed: {result['error']}")
        sys.exit(1)

    if result.get("status") == "setup_required":
        log("5", "Emulator not found — setup required", {
            "requires": result.get("requires"),
            "guided_setup": result.get("guided_setup"),
        })
        steps = result.get("manual_steps", [])
        if steps:
            print("         Manual setup steps:")
            for s in steps:
                print(f"           {s['step']}. {s['title']}")
                print(f"              $ {s['command']}")
        print()
        log("5", "Use --mode=agent to have the TA Coordinator guide your agent through setup")
        print("\n" + "=" * 60)
        print("  Dogfood: emulator setup needed")
        print("  Steps 1-4 passed, 5+ require emulator")
        print("=" * 60)
        sys.exit(0)

    run_id = result.get("run_id", "")
    engine = result.get("engine", "unknown")
    log("5", "QA flow started", {
        "run_id": run_id,
        "engine": engine,
        "view_url": result.get("view_url", ""),
    })

    # —— Step 6: Poll for completion ———————————————————————————————————————
    log("6", f"Polling pipeline status for {run_id}")
    poll_result = poll_pipeline(backend, token, run_id, timeout=args.pipeline_timeout)
    print()
    _require_success("6", poll_result)
    log("6", "Pipeline completed", {
        "status": poll_result.get("status"),
        "stage": poll_result.get("current_stage"),
    })

    # —— Step 7: Collect trace bundle ————————————————————————————————————————
    log("7", "Collecting trace bundle (retention.collect_trace_bundle)")
    trace_bundle = call_mcp(backend, token, "retention.collect_trace_bundle", {"run_id": run_id})
    _require_success("7", trace_bundle)
    log("7", "Trace bundle", trace_bundle)

    # —— Step 8: Emit verdict ————————————————————————————————————————————————
    log("8", "Emitting verdict (retention.emit_verdict)")
    verdict = call_mcp(backend, token, "retention.emit_verdict", {"run_id": run_id, "pass_threshold": 0.8})
    _require_success("8", verdict)
    log("8", "Verdict", {
        "verdict": verdict.get("verdict"),
        "pass_rate": verdict.get("pass_rate"),
        "total": verdict.get("total_tests"),
        "passed": verdict.get("passed"),
        "failed": verdict.get("failed"),
        "duration_s": verdict.get("duration_s"),
        "tool_calls": verdict.get("tool_call_count"),
    })

    # —— Steps 9-13: Judge / research / debug loop —————————————————————————
    debug_loop = run_failure_debug_loop(
        backend,
        token,
        run_id,
        args.url if args.flow_type == "web" else "",
        rerun_timeout=args.rerun_timeout,
        perform_rerun=not args.skip_rerun_loop,
    )

    if debug_loop.get("status") == "error":
        log("9", "Debug loop failed", debug_loop)
        sys.exit(1)

    failure_bundle = debug_loop.get("bundle", {})
    failure_count = debug_loop.get("failure_count", 0)
    log("9", "Compact failure bundle (retention.pipeline.failure_bundle)", {
        "failure_count": failure_count,
        "summary": failure_bundle.get("summary"),
        "rerun_command": failure_bundle.get("rerun_command"),
    })

    fix_context = debug_loop.get("fix_context")
    if fix_context:
        log("10", "Fix research context (retention.suggest_fix_context)", {
            "failure_count": fix_context.get("failure_count"),
            "categories": fix_context.get("categories"),
            "suggestion_count": len(fix_context.get("suggestions", [])),
        })

    feedback = debug_loop.get("feedback")
    if feedback:
        log("11", "Feedback package ready (ta.feedback_package)", {
            "failure_count": feedback.get("failure_count"),
            "suggested_files": feedback.get("suggested_files"),
            "prompt_length": len(feedback.get("prompt", "")),
        })
        print("  The 'prompt' field would be sent to the user's Claude Code to start fixing bugs.")

    rerun = debug_loop.get("rerun")
    rerun_status = debug_loop.get("rerun_status")
    comparison = debug_loop.get("comparison")
    if debug_loop.get("status") == "no_failures":
        log("10", "No failures — the research/debug loop was not needed")
    elif debug_loop.get("status") == "analysis_only":
        log("12", "Analysis loop complete — rerun intentionally skipped")
    elif debug_loop.get("status") == "rerun_not_started":
        log("12", "Rerun did not start", rerun or {"status": "unknown"})
    elif debug_loop.get("status") == "completed":
        log("12", "Rerun completed (retention.pipeline.rerun_failures)", {
            "rerun_run_id": rerun.get("run_id") if rerun else None,
            "status": rerun_status.get("status") if rerun_status else None,
            "stage": rerun_status.get("current_stage") if rerun_status else None,
        })
        log("13", "Before/after comparison (retention.compare_before_after)", {
            "fixes": comparison.get("fixes") if comparison else None,
            "regressions": comparison.get("regressions") if comparison else None,
            "metrics": comparison.get("metrics") if comparison else None,
        })

    # —— Step 14: Benchmark (optional) ——————————————————————————————————————
    if args.benchmark > 0:
        log("14", f"Starting benchmark: {args.benchmark} consecutive runs")
        resp = requests.post(
            f"{backend}/api/benchmarks/external/qa-flow/start",
            json={
                "flow_type": args.flow_type,
                "target": args.url if args.flow_type == "web" else args.app_package,
                "app_name": args.app_name,
                "run_count": args.benchmark,
                "timeout_per_run": 300,
            },
            timeout=30,
        )
        if resp.status_code == 200:
            bench_data = resp.json()
            bench_id = bench_data.get("benchmark_id", "")
            log("14", "Benchmark started", bench_data)

            while True:
                time.sleep(15)
                resp2 = requests.get(f"{backend}/api/benchmarks/external/qa-flow/{bench_id}", timeout=30)
                if resp2.status_code != 200:
                    break
                bdata = resp2.json()
                completed = bdata.get("completed_runs", 0)
                total = bdata.get("run_count", 0)
                print(f"         ... {completed}/{total} runs complete", end="\r")
                if bdata.get("status") != "running":
                    print()
                    log("14", "Benchmark complete", {
                        "aggregate": bdata.get("aggregate"),
                    })
                    break
        else:
            log("14", f"Benchmark start failed: {resp.status_code}")

    # —— Summary ———————————————————————————————————————————————————————————————
    print()
    print("=" * 60)
    print("  Dogfood complete!")
    print(f"  Signup identity: {args.signup_name} <{args.signup_email}>")
    print(f"  App: {args.app_name}")
    print(f"  Verdict: {verdict.get('verdict', 'unknown').upper()}")
    print(f"  Pass rate: {verdict.get('pass_rate', 0):.0%}")
    print(f"  Duration: {verdict.get('duration_s', '?')}s")
    print(f"  Tool calls: {verdict.get('tool_call_count', '?')}")
    print(f"  Debug loop: {debug_loop.get('status', 'unknown')}")
    if rerun and rerun.get("run_id"):
        print(f"  Rerun run_id: {rerun.get('run_id')}")
    if comparison:
        print(f"  Fixes confirmed: {len(comparison.get('fixes', []))}")
        print(f"  Regressions introduced: {len(comparison.get('regressions', []))}")
    print("=" * 60)


if __name__ == "__main__":
    main()
