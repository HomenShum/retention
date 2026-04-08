#!/usr/bin/env python3
"""
AndroidWorld Proof-of-Concept Runner.

Runs a subset of AndroidWorld tasks on available Android emulators
to validate the benchmark integration with Mobile MCP.

Usage:
    python -m backend.app.benchmarks.android_world.poc_runner

Or from the backend directory:
    python -c "import asyncio; from app.benchmarks.android_world.poc_runner import run_poc; asyncio.run(run_poc())"
"""

import asyncio
import json
import logging
import sys
from pathlib import Path

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent.parent))

from app.agents.device_testing.mobile_mcp_client import MobileMCPClient
from app.benchmarks.android_world.executor import AndroidWorldExecutor
from app.benchmarks.android_world.task_registry import AndroidWorldTaskRegistry

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


async def run_poc(device_ids: list[str] = None, tasks: list[str] = None):
    """
    Run AndroidWorld proof-of-concept benchmark.
    
    Args:
        device_ids: List of device IDs to test. If None, auto-detects.
        tasks: List of task names to run. If None, runs default POC tasks.
    """
    print("\n" + "="*60)
    print("🚀 AndroidWorld Benchmark - Proof of Concept")
    print("="*60 + "\n")
    
    # Initialize Mobile MCP client
    mcp_client = MobileMCPClient()
    
    try:
        print("📱 Starting Mobile MCP client...")
        await mcp_client.start()
        print("✅ Mobile MCP client started\n")
        
        # Get available devices
        if not device_ids:
            devices_str = await mcp_client.list_available_devices()
            print(f"📋 Available devices:\n{devices_str}\n")
            
            # Parse device IDs from the response
            # Expected format includes lines like "emulator-5556" or "emulator-5560"
            device_ids = []
            for line in devices_str.split("\n"):
                line = line.strip()
                if line.startswith("emulator-") or line.startswith("device:"):
                    # Extract device ID
                    parts = line.split()
                    if parts:
                        device_id = parts[0].replace("device:", "")
                        if device_id.startswith("emulator-"):
                            device_ids.append(device_id)
            
            if not device_ids:
                print("❌ No emulators found. Please start at least one emulator.")
                return
        
        print(f"🎯 Target devices: {device_ids}\n")
        
        # Initialize executor
        executor = AndroidWorldExecutor(mcp_client)
        
        # List available tasks
        registry = AndroidWorldTaskRegistry()
        print(f"📚 Available tasks ({registry.count}):")
        for name in registry.list_task_names():
            task = registry.get(name)
            print(f"   - {name} ({task.difficulty.value})")
        print()
        
        # Default POC tasks - simple ones that work on stock Android
        if not tasks:
            tasks = [
                "ClockStopWatchRunning",
                "OpenAppTaskEval",
                "SystemBluetoothTurnOn",
            ]
        
        print(f"🎬 Running {len(tasks)} tasks on {len(device_ids)} devices...\n")
        print("-"*60)
        
        # Run benchmark
        result = await executor.run_benchmark(
            task_names=tasks,
            device_ids=device_ids,
            parallel=True,
        )
        
        print("-"*60)
        print("\n📊 BENCHMARK RESULTS")
        print("="*60)
        print(f"Total Tasks:     {result.total_tasks}")
        print(f"Completed:       {result.completed_tasks}")
        print(f"Failed:          {result.failed_tasks}")
        print(f"Timeout:         {result.timeout_tasks}")
        print(f"Success Rate:    {result.success_rate:.1%}")
        print(f"Total Duration:  {result.total_duration_seconds:.1f}s")
        print("="*60)
        
        # Print per-task results
        print("\n📋 Task Details:")
        for tr in result.task_results:
            status_icon = "✅" if tr.status.value == "success" else "❌"
            print(f"   {status_icon} {tr.task_name} on {tr.device_id}")
            print(f"      Status: {tr.status.value}, Steps: {tr.steps_taken}, Duration: {tr.duration_seconds:.1f}s")
            if tr.error_message:
                print(f"      Error: {tr.error_message}")
        
        print("\n" + "="*60)
        print("✅ Proof of Concept Complete!")
        print("="*60 + "\n")
        
        return result
        
    finally:
        await mcp_client.stop()


if __name__ == "__main__":
    asyncio.run(run_poc())

