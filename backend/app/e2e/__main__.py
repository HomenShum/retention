"""
E2E Test Runner CLI

Usage:
    # Run smoke tests
    python -m app.e2e --suite smoke

    # Run single task
    python -m app.e2e --task ClockStopWatchRunning

    # Run with specific device
    python -m app.e2e --suite smoke --device emulator-5554

    # Run regression with verification
    python -m app.e2e --suite regression --verify --llm-judge

    # Verbose output
    python -m app.e2e --suite smoke -v
"""

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

# Load .env file from backend directory
try:
    from dotenv import load_dotenv
    # Try multiple .env locations
    backend_dir = Path(__file__).parent.parent.parent
    env_paths = [
        backend_dir / ".env",
        backend_dir.parent / ".env",
        Path.cwd() / ".env",
    ]
    for env_path in env_paths:
        if env_path.exists():
            load_dotenv(env_path)
            break
except ImportError:
    pass  # python-dotenv not installed

from .config import E2EConfig, TestSuite, TestSuiteType, DeviceConfig, SMOKE_SUITE
from .runner import E2ETestRunner, run_parallel_tests


def setup_logging(verbose: bool = False):
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def parse_args():
    parser = argparse.ArgumentParser(
        description="E2E Test Runner for Mobile Automation",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python -m app.e2e --suite smoke
  python -m app.e2e --task ClockStopWatchRunning
  python -m app.e2e --suite regression --verify --llm-judge
        """
    )
    
    # Test selection
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--suite", choices=["smoke", "regression", "device_testing"],
                       help="Run a predefined test suite")
    group.add_argument("--task", help="Run a single task by name")
    group.add_argument("--tasks", nargs="+", help="Run multiple tasks")
    
    # Device options
    parser.add_argument("--device", default="emulator-5554",
                        help="Device ID (default: emulator-5554)")
    parser.add_argument("--devices", nargs="+",
                        help="Multiple device IDs for parallel execution")
    parser.add_argument("--parallel", "-p", action="store_true",
                        help="Run tests in parallel across multiple devices")
    parser.add_argument("--no-auto-launch", action="store_true",
                        help="Don't auto-launch emulator")
    
    # Verification options
    parser.add_argument("--verify", action="store_true",
                        help="Enable state verification")
    parser.add_argument("--llm-judge", action="store_true",
                        help="Enable LLM-as-judge evaluation")
    parser.add_argument("--no-screenshots", action="store_true",
                        help="Disable screenshots")

    # Executor options
    parser.add_argument("--agent", action="store_true", default=True,
                        help="Use LLM agent-based executor (default)")
    parser.add_argument("--scripted", action="store_true",
                        help="Use scripted executor instead of agent")

    # Output options
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="Verbose output")
    parser.add_argument("--output", type=str, default="e2e_results",
                        help="Output directory for results")
    
    return parser.parse_args()


def check_api_key() -> bool:
    """Check if OpenAI API key is available."""
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if api_key:
        masked = api_key[:7] + "..." + api_key[-4:] if len(api_key) > 15 else "***"
        print(f"✓ OPENAI_API_KEY: {masked}")
        return True
    return False


def print_env_status():
    """Print environment status."""
    print("\n📋 Environment Check")
    print("-" * 40)

    # Check API key
    has_key = check_api_key()
    if not has_key:
        print("✗ OPENAI_API_KEY: NOT SET")
        print("  └─ LLM verification will be disabled")
        print("  └─ Set via: export OPENAI_API_KEY='sk-...'")
        print("  └─ Or add to backend/.env file")

    # Check Android SDK
    android_home = os.environ.get("ANDROID_HOME", os.environ.get("ANDROID_SDK_ROOT", ""))
    if android_home:
        print(f"✓ ANDROID_HOME: {android_home}")
    else:
        print("? ANDROID_HOME: Not explicitly set (will auto-detect)")

    print("-" * 40 + "\n")
    return has_key


async def main():
    args = parse_args()
    setup_logging(args.verbose)

    # Check environment
    has_api_key = print_env_status()

    # Build config
    if args.suite:
        config = E2EConfig.for_suite(args.suite)
    else:
        # Custom task(s)
        tasks = args.tasks if args.tasks else [args.task]
        suite = TestSuite(
            name="custom",
            type=TestSuiteType.CUSTOM,
            description="Custom test run",
            tasks=tasks,
            verify_state=args.verify,
            use_llm_judge=args.llm_judge,
        )
        config = E2EConfig(suite=suite)

    # Apply options
    config.device.device_id = args.device
    config.device.auto_launch = not args.no_auto_launch
    config.screenshots = not args.no_screenshots
    config.verbose = args.verbose

    if args.verify:
        config.suite.verify_state = True
    if args.llm_judge:
        # Only enable if API key is available
        if has_api_key:
            config.suite.use_llm_judge = True
        else:
            print("⚠️  LLM judge disabled: OPENAI_API_KEY not set\n")
            config.suite.use_llm_judge = False

    # Executor option
    if args.scripted:
        config.use_agent_executor = False
    else:
        config.use_agent_executor = True  # Agent by default

    # Determine devices for parallel execution
    device_ids = args.devices if args.devices else [args.device]

    # Auto-detect available emulators for parallel mode
    if args.parallel and not args.devices:
        import subprocess
        try:
            adb_result = subprocess.run(
                ["adb", "devices"], capture_output=True, text=True, timeout=5
            )
            lines = adb_result.stdout.strip().split("\n")[1:]  # Skip header
            device_ids = [
                line.split()[0] for line in lines
                if line.strip() and "device" in line and "offline" not in line
            ]
            if not device_ids:
                device_ids = [args.device]
        except Exception:
            device_ids = [args.device]

    # Run tests
    executor_type = "🤖 AGENT (LLM-driven)" if config.use_agent_executor else "📜 SCRIPTED"
    parallel_mode = args.parallel or len(device_ids) > 1

    print("🚀 E2E Test Runner")
    print("=" * 60)
    print(f"Suite:    {config.suite.name}")
    print(f"Tasks:    {len(config.suite.tasks)}")
    print(f"Device:   {', '.join(device_ids)}")
    print(f"Parallel: {'✓ Yes' if parallel_mode else 'No'}")
    print(f"Executor: {executor_type}")
    print(f"Verify:   {config.suite.verify_state}")
    print(f"LLM Judge: {config.suite.use_llm_judge}")
    print("=" * 60 + "\n")

    if parallel_mode and len(device_ids) > 1:
        # Parallel execution across multiple devices
        result = await run_parallel_tests(config.suite, device_ids, config)
    else:
        # Sequential execution on single device
        config.device.device_id = device_ids[0]
        runner = E2ETestRunner(config)
        result = await runner.run()

    # Enhanced result summary with boolean metrics
    print("\n" + "=" * 60)
    print("📊 DETAILED RESULTS")
    print("=" * 60)
    for test_result in result.test_results:
        status = "✅ PASS" if test_result.passed else "❌ FAIL"
        print(f"\n{status} {test_result.task_name}")
        print(f"  Duration: {test_result.duration_seconds:.1f}s")

        # Display steps and token usage
        if hasattr(test_result, 'steps_taken') and test_result.steps_taken > 0:
            print(f"  Steps: {test_result.steps_taken}")
        if hasattr(test_result, 'token_usage') and test_result.token_usage:
            t = test_result.token_usage
            print(f"  Tokens: {t['total_tokens']:,} ({t['prompt_tokens']:,} in / {t['completion_tokens']:,} out)")

        if test_result.error:
            print(f"  Error: {test_result.error[:100]}...")
        if hasattr(test_result, 'verification') and test_result.verification:
            v = test_result.verification
            print(f"  Verification: {'✓' if v.passed else '✗'}")

            # Display boolean metrics if available
            if hasattr(v, 'llm_judge_metrics') and v.llm_judge_metrics:
                m = v.llm_judge_metrics
                print(f"  ├─ Task Understood:     {'✓' if m.task_understood else '✗'}")
                print(f"  ├─ Correct App Opened:  {'✓' if m.correct_app_opened else '✗'}")
                print(f"  ├─ Target Found:        {'✓' if m.target_element_found else '✗'}")
                print(f"  ├─ Action Executed:     {'✓' if m.action_executed else '✗'}")
                print(f"  ├─ Final State Correct: {'✓' if m.final_state_correct else '✗'}")
                print(f"  ├─ No Errors:           {'✓' if m.no_errors_occurred else '✗'}")
                if m.reasoning:
                    # Truncate reasoning for display
                    reasoning = m.reasoning[:120] + "..." if len(m.reasoning) > 120 else m.reasoning
                    print(f"  └─ Reasoning: {reasoning}")

    print("\n" + "=" * 60)
    print(f"Success Rate: {result.success_rate * 100:.0f}%")
    print("=" * 60 + "\n")

    # Exit code based on success
    sys.exit(0 if result.success_rate >= 0.9 else 1)


if __name__ == "__main__":
    asyncio.run(main())

