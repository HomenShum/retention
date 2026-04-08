"""
Comprehensive AndroidWorld Benchmark Test

Tests the complete integration including:
- Expanded task registry (39 tasks)
- PRD ingestion
- Golden Bug generation
- Execution on device fleet
"""
import asyncio
import json
from datetime import datetime

import pytest

from app.agents.device_testing.mobile_mcp_client import MobileMCPClient
from app.benchmarks.android_world.task_registry import AndroidWorldTaskRegistry
from app.benchmarks.android_world.executor import AndroidWorldExecutor
from app.benchmarks.prd_ingestion import PRDProcessor


@pytest.mark.asyncio
async def test_expanded_benchmark():
    """Test expanded AndroidWorld benchmark with 39 tasks"""
    print("\n" + "="*70)
    print("🧪 Android World Expanded Benchmark Test")
    print("="*70)
    
    # Get MCP client
    mobile_client = MobileMCPClient()
    await mobile_client.start()
    
    # Get devices
    devices = await mobile_client.list_devices()
    device_ids = [d["id"] for d in devices if d["status"] == "online"]
    
    print(f"\n📱 Available devices: {device_ids}")
    
    if len(device_ids) < 2:
        print("⚠️  Warning: Less than 2 devices available")
        return
    
    # Initialize registry and executor
    registry = AndroidWorldTaskRegistry()
    executor = AndroidWorldExecutor(mobile_client)
    
    print(f"\n📊 Task Registry Stats:")
    print(f"  Total tasks: {registry.count}")
    
    # Test representative tasks from each category
    test_tasks = [
        # Data Entry
        "ClockStopWatchRunning",
        "MarkorCreateNote",
        "CalendarAddEvent",
        "ContactsAddContact",
        
        # Multi-app
        "SystemWifiTurnOn",
        "CameraTakePhoto",
        "BrowserOpenPage",
        
        # Screen Reading
        "MarkorViewNoteList",
        "CameraViewPhotos",
    ]
    
    print(f"\n🎯 Running {len(test_tasks)} representative tasks on {len(device_ids)} devices")
    print(f"   Total executions: {len(test_tasks) * len(device_ids)}")
    
    # Run benchmark
    result = await executor.run_benchmark(
        task_names=test_tasks,
        device_ids=device_ids[:2],  # Use first 2 devices
        parallel=True
    )
    
    # Display results
    print(f"\n✅ Benchmark Complete!")
    print(f"  Success Rate: {result.success_rate:.1%}")
    print(f"  Total Duration: {result.total_duration_seconds:.2f}s")
    print(f"  Completed: {result.completed_tasks}")
    print(f"  Failed: {result.failed_tasks}")
    
    # Show per-task results
    print(f"\n📋 Task Results:")
    for tr in result.task_results:
        status_emoji = "✅" if tr.status.value == "success" else "❌"
        print(f"  {status_emoji} {tr.task_name} on {tr.device_id}: {tr.duration_seconds:.2f}s")
    
    await mobile_client.stop()
    
    return result


@pytest.mark.asyncio
async def test_prd_ingestion():
    """Test PRD ingestion and Golden Bug generation"""
    print("\n" + "="*70)
    print("📝 PRD Ingestion & Golden Bug Generation Test")
    print("="*70)
    
    processor = PRDProcessor()
    
    # Sample PRD for a recipe app
    prd_text = """
    Product Requirements: Recipe Manager App
    
    As a user, I want to create new recipes so that I can save my favorite dishes.
    
    As a user, I want to add ingredients to recipes so that I have a complete recipe.
    
    As a user, I want to delete recipes I no longer use.
    
    User should be able to search for recipes by name.
    
    User should be able to view recipe details including ingredients and instructions.
    
    Acceptance Criteria:
    - Recipes must have a name and category
    - Ingredients are listed with quantities
    - Instructions are shown step-by-step
    - Deleted recipes are removed permanently
    """
    
    # Ingest PRD
    result = await processor.ingest_prd(prd_text, "RECIPE-PRD-001")
    
    print(f"\n📊 PRD Ingestion Results:")
    print(f"  PRD ID: {result.prd_id}")
    print(f"  User Stories: {result.summary['user_stories_count']}")
    print(f"  Test Cases: {result.summary['test_cases_count']}")
    print(f"  Golden Bugs: {result.summary['golden_bugs_count']}")
    print(f"  Apps Covered: {', '.join(result.summary['apps_covered'])}")
    print(f"  Categories: {', '.join(result.summary['categories'])}")
    
    print(f"\n🐛 Generated Golden Bugs:")
    for gb in result.golden_bugs[:5]:  # Show first 5
        print(f"  - {gb.id}")
        print(f"    Title: {gb.title}")
        print(f"    Priority: {gb.priority}")
        print(f"    Steps: {len(gb.test_steps)}")
    
    if len(result.golden_bugs) > 5:
        print(f"  ... and {len(result.golden_bugs) - 5} more")
    
    return result


@pytest.mark.asyncio
async def test_prd_execution():
    """Test executing PRD-generated tests on devices"""
    print("\n" + "="*70)
    print("🚀 PRD-Generated Test Execution")
    print("="*70)
    
    processor = PRDProcessor()
    mobile_client = MobileMCPClient()
    await mobile_client.start()
    
    # Simple note-taking PRD
    prd_text = """
    As a user, I want to create notes so that I can capture ideas.
    As a user, I want to delete notes I don't need.
    """
    
    result = await processor.ingest_prd(prd_text, "NOTES-PRD-EXEC-001")
    
    # Get devices
    devices = await mobile_client.list_devices()
    device_ids = [d["id"] for d in devices if d["status"] == "online"][:2]
    
    print(f"\n📱 Devices: {device_ids}")
    print(f"🐛 Golden Bugs to execute: {len(result.golden_bugs)}")
    
    # Execute first 2 Golden Bugs
    for gb in result.golden_bugs[:2]:
        print(f"\n▶️  Executing: {gb.id}")
        
        exec_result = await processor.execute_golden_bug_on_devices(
            golden_bug=gb,
            device_ids=device_ids,
            mobile_mcp_client=mobile_client
        )
        
        print(f"  Overall Success: {exec_result['overall_success']}")
        print(f"  Success Rate: {exec_result['success_rate']:.1%}")
        
        for dr in exec_result['device_results']:
            status = "✅" if dr['success'] else "❌"
            print(f"    {status} {dr['device_id']}")
    
    await mobile_client.stop()


async def main():
    """Run all comprehensive tests"""
    start_time = datetime.now()
    
    print("\n🎯 Starting Comprehensive AndroidWorld Integration Tests")
    print(f"⏰ Started at: {start_time.strftime('%Y-%m-%d %H:%M:%S')}")
    
    try:
        # Test 1: Expanded benchmark
        benchmark_result = await test_expanded_benchmark()
        
        # Test 2: PRD ingestion
        prd_result = await test_prd_ingestion()
        
        # Test 3: PRD execution
        await test_prd_execution()
        
        # Summary
        end_time = datetime.now()
        duration = (end_time - start_time).total_seconds()
        
        print("\n" + "="*70)
        print("🎉 All Tests Complete!")
        print("="*70)
        print(f"⏱️  Total Duration: {duration:.2f}s")
        print(f"✅ Expanded Benchmark: {benchmark_result.success_rate:.1%} success rate")
        print(f"✅ PRD Ingestion: {prd_result.summary['golden_bugs_count']} Golden Bugs generated")
        print(f"✅ PRD Execution: Tests executed on devices")
        
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())
