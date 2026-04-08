"""
AndroidWorld Test Case Generator

Generates AndroidWorld-style test cases from PRD specifications
and user stories.
"""
from typing import List, Dict, Optional
from dataclasses import dataclass
import json
import re


@dataclass
class UserStory:
    """Represents a user story extracted from PRD"""
    id: str
    title: str
    description: str
    acceptance_criteria: List[str]
    priority: str = "medium"


@dataclass
class TestScenario:
    """Test scenario derived from user story"""
    story_id: str
    scenario_name: str
    given: List[str]  # Preconditions
    when: List[str]   # Actions
    then: List[str]   # Expected outcomes


@dataclass
class GeneratedTestCase:
    """Test case in AndroidWorld format"""
    task_id: str
    category: str
    app: str
    description: str
    actions: List[Dict[str, any]]
    expected_result: str
    golden_bug_id: Optional[str] = None


class TestCaseGenerator:
    """Generates test cases from PRDs and user stories"""
    
    def __init__(self):
        self.app_mappings = {
            "note": "markor",
            "notes": "markor",
            "calendar": "simple_calendar",
            "schedule": "simple_calendar",
            "expense": "expense_tracker",
            "budget": "expense_tracker",
            "recipe": "recipe_keeper",
            "cooking": "recipe_keeper",
            "alarm": "clock",
            "timer": "clock",
            "stopwatch": "clock",
            "contact": "contacts",
            "phone": "contacts",
            "camera": "camera",
            "photo": "camera",
            "wifi": "settings",
            "bluetooth": "settings",
            "browser": "browser",
            "web": "browser",
        }
        
        self.category_keywords = {
            "data_entry": ["create", "add", "enter", "input", "fill"],
            "screen_reading": ["view", "read", "check", "verify", "display"],
            "multi_app": ["navigate", "switch", "share", "between"],
            "complex_ui": ["scroll", "swipe", "select", "filter", "search"],
        }
    
    def extract_user_stories_from_prd(self, prd_text: str) -> List[UserStory]:
        """
        Extract user stories from PRD text
        
        Pattern matching for common formats:
        - As a [role], I want [goal] so that [benefit]
        - User should be able to [action]
        """
        stories = []
        
        # Pattern 1: As a ... I want ... so that ...
        pattern1 = r"As a\s+([^,]+),\s*I want\s+([^,]+?)(?:,?\s*so that\s+(.+?))?(?:\.|$)"
        matches1 = re.finditer(pattern1, prd_text, re.IGNORECASE | re.MULTILINE)
        
        for i, match in enumerate(matches1):
            role = match.group(1).strip()
            goal = match.group(2).strip()
            benefit = match.group(3).strip() if match.group(3) else ""
            
            stories.append(UserStory(
                id=f"US-{i+1:03d}",
                title=f"{role} - {goal[:50]}",
                description=f"As a {role}, I want {goal}" + (f" so that {benefit}" if benefit else ""),
                acceptance_criteria=[],
                priority="medium"
            ))
        
        # Pattern 2: User should be able to ...
        pattern2 = r"User(?:s)?\s+should\s+be\s+able\s+to\s+(.+?)(?:\.|$)"
        matches2 = re.finditer(pattern2, prd_text, re.IGNORECASE | re.MULTILINE)
        
        offset = len(stories)
        for i, match in enumerate(matches2):
            capability = match.group(1).strip()
            
            stories.append(UserStory(
                id=f"US-{offset+i+1:03d}",
                title=f"User capability - {capability[:50]}",
                description=f"User should be able to {capability}",
                acceptance_criteria=[],
                priority="medium"
            ))
        
        # Extract acceptance criteria (look for bullet points or numbered lists)
        for story in stories:
            # Find context around the story
            story_index = prd_text.find(story.description)
            if story_index != -1:
                # Look ahead for criteria
                context = prd_text[story_index:story_index+500]
                criteria = re.findall(r"(?:^|\n)\s*[-*•]\s*(.+?)(?=\n|$)", context, re.MULTILINE)
                story.acceptance_criteria = criteria[:5]  # Limit to 5 criteria
        
        return stories
    
    def generate_test_scenarios(self, story: UserStory) -> List[TestScenario]:
        """Generate test scenarios from a user story"""
        scenarios = []
        
        # Base scenario from story description
        scenario = TestScenario(
            story_id=story.id,
            scenario_name=f"Basic - {story.title[:40]}",
            given=["User has opened the app", "App is in default state"],
            when=self._extract_actions_from_description(story.description),
            then=self._extract_expected_outcomes(story.description, story.acceptance_criteria)
        )
        scenarios.append(scenario)
        
        # Additional scenarios from acceptance criteria
        for i, criterion in enumerate(story.acceptance_criteria):
            scenarios.append(TestScenario(
                story_id=story.id,
                scenario_name=f"AC{i+1} - {criterion[:40]}",
                given=["User has opened the app"],
                when=self._extract_actions_from_description(criterion),
                then=[criterion]
            ))
        
        return scenarios
    
    def _extract_actions_from_description(self, text: str) -> List[str]:
        """Extract action verbs and objects from description"""
        actions = []
        
        # Common action patterns
        action_patterns = [
            (r"(create|add|enter|input)\s+(?:a\s+)?(.+?)(?=\s+with|\s+to|\s+and|\s+so|$)", "create"),
            (r"(edit|update|modify)\s+(?:the\s+)?(.+?)(?=\s+with|\s+to|\s+and|$)", "edit"),
            (r"(delete|remove)\s+(?:the\s+)?(.+?)(?=\s+from|\s+and|$)", "delete"),
            (r"(view|check|verify)\s+(?:the\s+)?(.+?)(?=\s+and|$)", "view"),
            (r"(navigate|go)\s+to\s+(.+?)(?=\s+and|$)", "navigate"),
        ]
        
        for pattern, action_type in action_patterns:
            matches = re.finditer(pattern, text, re.IGNORECASE)
            for match in matches:
                object_text = match.group(2).strip()
                actions.append(f"{action_type.capitalize()} {object_text}")
        
        # Fallback: if no actions found, use the whole description
        if not actions:
            actions = [text[:100]]
        
        return actions
    
    def _extract_expected_outcomes(self, description: str, criteria: List[str]) -> List[str]:
        """Extract expected outcomes from description and criteria"""
        outcomes = []
        
        # Look for "so that" clauses
        so_that = re.search(r"so that\s+(.+?)(?:\.|$)", description, re.IGNORECASE)
        if so_that:
            outcomes.append(so_that.group(1).strip())
        
        # Add acceptance criteria as outcomes
        outcomes.extend(criteria[:3])  # Limit to 3
        
        # Default outcome
        if not outcomes:
            outcomes = ["Action completed successfully"]
        
        return outcomes
    
    def scenario_to_android_world_task(self, scenario: TestScenario) -> GeneratedTestCase:
        """Convert test scenario to AndroidWorld task format"""
        
        # Determine app from actions
        app = self._infer_app_from_actions(scenario.when)
        
        # Determine category
        category = self._infer_category_from_actions(scenario.when)
        
        # Generate action sequence
        actions = self._generate_action_sequence(scenario.when, app)
        
        task_id = f"GENERATED_{scenario.story_id}_{category.upper()}"
        
        return GeneratedTestCase(
            task_id=task_id,
            category=category,
            app=app,
            description=f"{scenario.scenario_name}: {', '.join(scenario.when)}",
            actions=actions,
            expected_result=" AND ".join(scenario.then),
            golden_bug_id=None  # Will be assigned when stored
        )
    
    def _infer_app_from_actions(self, actions: List[str]) -> str:
        """Infer which app to use based on actions"""
        text = " ".join(actions).lower()
        
        for keyword, app in self.app_mappings.items():
            if keyword in text:
                return app
        
        return "unknown"
    
    def _infer_category_from_actions(self, actions: List[str]) -> str:
        """Infer test category from actions"""
        text = " ".join(actions).lower()
        
        for category, keywords in self.category_keywords.items():
            if any(kw in text for kw in keywords):
                return category
        
        return "general"
    
    def _generate_action_sequence(self, actions: List[str], app: str) -> List[Dict]:
        """Generate MCP action sequence from action descriptions"""
        sequence = []
        
        # Always start with launch_app
        sequence.append({
            "type": "launch_app",
            "app": app,
            "delay": 2
        })
        
        for action in actions:
            action_lower = action.lower()
            
            # Parse action type and generate appropriate MCP actions
            if "create" in action_lower or "add" in action_lower:
                # Click "Add" or "New" button
                sequence.append({
                    "type": "click",
                    "description": "new_button",
                    "delay": 1
                })
                # Type content
                sequence.append({
                    "type": "type",
                    "text": f"Test {action}",
                    "delay": 0.5
                })
                # Save
                sequence.append({
                    "type": "click",
                    "description": "save_button",
                    "delay": 1
                })
            
            elif "edit" in action_lower or "update" in action_lower:
                # Click item
                sequence.append({
                    "type": "click",
                    "description": "first_item",
                    "delay": 1
                })
                # Edit
                sequence.append({
                    "type": "type",
                    "text": f"Updated {action}",
                    "delay": 0.5
                })
                # Save
                sequence.append({
                    "type": "click",
                    "description": "save_button",
                    "delay": 1
                })
            
            elif "delete" in action_lower or "remove" in action_lower:
                # Long press on item
                sequence.append({
                    "type": "click",
                    "description": "first_item",
                    "hold_duration": 1.5,
                    "delay": 1
                })
                # Confirm delete
                sequence.append({
                    "type": "click",
                    "description": "delete_button",
                    "delay": 1
                })
            
            elif "view" in action_lower or "check" in action_lower:
                # Click to view
                sequence.append({
                    "type": "click",
                    "description": "first_item",
                    "delay": 1
                })
            
            elif "navigate" in action_lower or "go to" in action_lower:
                # Swipe or navigate
                sequence.append({
                    "type": "swipe",
                    "direction": "left",
                    "delay": 1
                })
        
        # Add a final screenshot action
        sequence.append({
            "type": "screenshot",
            "description": "final_state"
        })
        
        return sequence
    
    def generate_tests_from_prd(self, prd_text: str) -> List[GeneratedTestCase]:
        """
        Full pipeline: PRD -> User Stories -> Scenarios -> Test Cases
        """
        # Step 1: Extract user stories
        stories = self.extract_user_stories_from_prd(prd_text)
        
        # Step 2: Generate scenarios for each story
        all_scenarios = []
        for story in stories:
            scenarios = self.generate_test_scenarios(story)
            all_scenarios.extend(scenarios)
        
        # Step 3: Convert scenarios to AndroidWorld tasks
        test_cases = []
        for scenario in all_scenarios:
            task = self.scenario_to_android_world_task(scenario)
            test_cases.append(task)
        
        return test_cases


def main():
    """Example usage"""
    generator = TestCaseGenerator()
    
    # Example PRD
    prd_example = """
    Product Requirements: Note Taking App
    
    As a user, I want to create new notes so that I can capture my ideas.
    
    As a user, I want to edit existing notes so that I can update my content.
    
    User should be able to delete notes they no longer need.
    
    User should be able to view all their notes in a list.
    
    Acceptance Criteria:
    - Notes must have a title and body
    - Notes are saved automatically
    - Deleted notes are removed permanently
    """
    
    # Generate test cases
    test_cases = generator.generate_tests_from_prd(prd_example)
    
    print(f"Generated {len(test_cases)} test cases:")
    for tc in test_cases:
        print(f"\n{tc.task_id}:")
        print(f"  App: {tc.app}")
        print(f"  Category: {tc.category}")
        print(f"  Description: {tc.description}")
        print(f"  Actions: {len(tc.actions)} steps")
        print(f"  Expected: {tc.expected_result}")


if __name__ == "__main__":
    main()
