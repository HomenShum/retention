"""
PRD Ingestion and Test Generation Pipeline

Ingests Product Requirement Documents and generates Golden Bugs
with AndroidWorld test cases.
"""
from typing import List, Dict, Optional
from dataclasses import dataclass, asdict
from datetime import datetime
import asyncio
import json

from app.benchmarks.android_world.test_generator import (
    TestCaseGenerator,
    GeneratedTestCase,
    UserStory
)


@dataclass
class GoldenBug:
    """Golden Bug representation"""
    id: str
    title: str
    description: str
    expected_result: str
    test_steps: List[Dict]
    category: str
    priority: str
    tags: List[str]
    source: str = "prd_generation"
    created_at: str = None
    
    def __post_init__(self):
        if self.created_at is None:
            self.created_at = datetime.now().isoformat()


@dataclass
class PRDIngestionResult:
    """Result of PRD ingestion"""
    prd_id: str
    user_stories: List[UserStory]
    generated_tests: List[GeneratedTestCase]
    golden_bugs: List[GoldenBug]
    summary: Dict


class PRDProcessor:
    """Processes PRDs and generates Golden Bugs"""
    
    def __init__(self):
        self.test_generator = TestCaseGenerator()
        self.golden_bugs_storage: List[GoldenBug] = []
    
    async def ingest_prd(
        self,
        prd_text: str,
        prd_id: Optional[str] = None
    ) -> PRDIngestionResult:
        """
        Main pipeline: PRD -> User Stories -> Test Cases -> Golden Bugs
        
        Args:
            prd_text: Full PRD text content
            prd_id: Optional identifier for the PRD
        
        Returns:
            PRDIngestionResult with all generated artifacts
        """
        if prd_id is None:
            prd_id = f"PRD-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
        
        # Step 1: Extract user stories from PRD
        user_stories = self.test_generator.extract_user_stories_from_prd(prd_text)
        
        # Step 2: Generate test cases from PRD
        test_cases = self.test_generator.generate_tests_from_prd(prd_text)
        
        # Step 3: Convert test cases to Golden Bugs
        golden_bugs = self._convert_to_golden_bugs(test_cases, prd_id)
        
        # Step 4: Store Golden Bugs
        self.golden_bugs_storage.extend(golden_bugs)
        
        # Generate summary
        summary = {
            "prd_id": prd_id,
            "user_stories_count": len(user_stories),
            "test_cases_count": len(test_cases),
            "golden_bugs_count": len(golden_bugs),
            "apps_covered": list(set(tc.app for tc in test_cases)),
            "categories": list(set(tc.category for tc in test_cases))
        }
        
        return PRDIngestionResult(
            prd_id=prd_id,
            user_stories=user_stories,
            generated_tests=test_cases,
            golden_bugs=golden_bugs,
            summary=summary
        )
    
    def _convert_to_golden_bugs(
        self,
        test_cases: List[GeneratedTestCase],
        prd_id: str
    ) -> List[GoldenBug]:
        """Convert AndroidWorld test cases to Golden Bug format"""
        golden_bugs = []
        
        for i, tc in enumerate(test_cases):
            bug_id = f"{prd_id}-GB-{i+1:03d}"
            
            # Convert actions to test steps
            test_steps = []
            for j, action in enumerate(tc.actions):
                step = {
                    "step_number": j + 1,
                    "action": action.get("type", "unknown"),
                    "description": action.get("description", ""),
                    "details": action
                }
                test_steps.append(step)
            
            # Determine priority based on category
            priority_map = {
                "data_entry": "high",
                "screen_reading": "medium",
                "multi_app": "high",
                "complex_ui": "medium",
                "general": "low"
            }
            priority = priority_map.get(tc.category, "medium")
            
            # Create Golden Bug
            golden_bug = GoldenBug(
                id=bug_id,
                title=f"[{tc.app.upper()}] {tc.description[:60]}",
                description=tc.description,
                expected_result=tc.expected_result,
                test_steps=test_steps,
                category=tc.category,
                priority=priority,
                tags=[tc.app, tc.category, "auto-generated", prd_id],
                source="prd_generation"
            )
            
            # Link back to test case
            tc.golden_bug_id = bug_id
            
            golden_bugs.append(golden_bug)
        
        return golden_bugs
    
    async def execute_golden_bug_on_devices(
        self,
        golden_bug: GoldenBug,
        device_ids: List[str],
        mobile_mcp_client
    ) -> Dict:
        """
        Execute a Golden Bug test on multiple devices
        
        Args:
            golden_bug: The Golden Bug to execute
            device_ids: List of device IDs to test on
            mobile_mcp_client: Mobile MCP client instance
        
        Returns:
            Execution results
        """
        results = {
            "golden_bug_id": golden_bug.id,
            "device_results": [],
            "overall_success": False
        }
        
        # Execute on each device in parallel
        tasks = []
        for device_id in device_ids:
            task = self._execute_on_single_device(
                golden_bug,
                device_id,
                mobile_mcp_client
            )
            tasks.append(task)
        
        device_results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Aggregate results
        successful_count = 0
        for i, result in enumerate(device_results):
            device_result = {
                "device_id": device_ids[i],
                "success": False,
                "error": None
            }
            
            if isinstance(result, Exception):
                device_result["error"] = str(result)
            else:
                device_result = result
                if result.get("success"):
                    successful_count += 1
            
            results["device_results"].append(device_result)
        
        results["overall_success"] = successful_count == len(device_ids)
        results["success_rate"] = successful_count / len(device_ids) if device_ids else 0
        
        return results
    
    async def _execute_on_single_device(
        self,
        golden_bug: GoldenBug,
        device_id: str,
        mobile_mcp_client
    ) -> Dict:
        """Execute Golden Bug on a single device"""
        try:
            start_time = datetime.now()
            
            # Execute each test step
            for step in golden_bug.test_steps:
                action = step["details"]
                
                if action["type"] == "launch_app":
                    await mobile_mcp_client.launch_app(
                        device_id=device_id,
                        app_name=action["app"]
                    )
                elif action["type"] == "click":
                    await mobile_mcp_client.click(
                        device_id=device_id,
                        description=action.get("description", "")
                    )
                elif action["type"] == "type":
                    await mobile_mcp_client.type_text(
                        device_id=device_id,
                        text=action.get("text", "")
                    )
                elif action["type"] == "swipe":
                    await mobile_mcp_client.swipe(
                        device_id=device_id,
                        direction=action.get("direction", "up")
                    )
                elif action["type"] == "screenshot":
                    await mobile_mcp_client.take_screenshot(
                        device_id=device_id
                    )
                
                # Delay between actions
                if "delay" in action:
                    await asyncio.sleep(action["delay"])
            
            duration = (datetime.now() - start_time).total_seconds()
            
            return {
                "device_id": device_id,
                "success": True,
                "duration": duration,
                "error": None
            }
        
        except Exception as e:
            return {
                "device_id": device_id,
                "success": False,
                "duration": 0,
                "error": str(e)
            }
    
    def get_golden_bugs(
        self,
        prd_id: Optional[str] = None,
        category: Optional[str] = None,
        priority: Optional[str] = None
    ) -> List[GoldenBug]:
        """Retrieve Golden Bugs with optional filters"""
        bugs = self.golden_bugs_storage
        
        if prd_id:
            bugs = [b for b in bugs if prd_id in b.tags]
        
        if category:
            bugs = [b for b in bugs if b.category == category]
        
        if priority:
            bugs = [b for b in bugs if b.priority == priority]
        
        return bugs
    
    def export_golden_bugs_json(self, bugs: List[GoldenBug]) -> str:
        """Export Golden Bugs to JSON format"""
        bugs_dict = [asdict(bug) for bug in bugs]
        return json.dumps(bugs_dict, indent=2)


# Global processor instance
_processor = None

def get_prd_processor() -> PRDProcessor:
    """Get singleton PRD processor"""
    global _processor
    if _processor is None:
        _processor = PRDProcessor()
    return _processor


async def main():
    """Example usage"""
    processor = PRDProcessor()
    
    prd_text = """
    Product Requirements: Calendar Event Manager
    
    As a user, I want to create calendar events so that I can track my schedule.
    
    As a user, I want to view my upcoming events so that I know my schedule.
    
    User should be able to delete events they no longer need.
    
    Acceptance Criteria:
    - Events must have title, date, and time
    - Events are displayed chronologically
    - Past events are archived
    """
    
    result = await processor.ingest_prd(prd_text, "CAL-PRD-001")
    
    print(f"\nPRD Ingestion Result:")
    print(f"User Stories: {result.summary['user_stories_count']}")
    print(f"Test Cases: {result.summary['test_cases_count']}")
    print(f"Golden Bugs: {result.summary['golden_bugs_count']}")
    print(f"Apps: {result.summary['apps_covered']}")
    
    print(f"\nGenerated Golden Bugs:")
    for gb in result.golden_bugs:
        print(f"  - {gb.id}: {gb.title}")
        print(f"    Priority: {gb.priority}, Steps: {len(gb.test_steps)}")


if __name__ == "__main__":
    asyncio.run(main())
