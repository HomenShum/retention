# OAVR Sub-Agents for Device Testing

This directory contains three specialized LLM-based sub-agents that implement the **Observe-Act-Verify-Reflect (OAVR)** pattern for autonomous mobile device testing.

## Overview

The OAVR pattern improves autonomous navigation by adding structured decision-making and error recovery through specialized sub-agents. All logic is **LLM-based** with **NO hard-coded heuristics** and uses **boolean decisions only** (no arbitrary 0-1 scores).

## Architecture

```
Device Testing Agent (Main)
├── Screen State Classifier Agent
├── Action Verifier Agent
└── Failure Diagnosis Agent
```

The main device testing agent can hand off to these sub-agents at appropriate points during autonomous navigation.

## Sub-Agents

### 1. Screen Classifier Agent (`screen_classifier_agent.py`)

**Purpose:** Dynamically classify screen states using LLM reasoning

**Input:**
- `elements`: Array of UI elements from `list_elements_on_screen`
- `task_goal`: Current user goal
- `previous_state`: Previous screen state (optional)
- `action_history`: Recent actions (optional)

**Output:**
```json
{
    "state_type": "descriptive name (e.g., 'permission dialog', 'search results')",
    "is_unexpected": true/false,
    "key_elements": [
        {
            "type": "element type",
            "text": "element text",
            "label": "element label",
            "reason": "why this element is important"
        }
    ],
    "confidence": "HIGH|MEDIUM|LOW",
    "reasoning": "brief explanation"
}
```

**Key Features:**
- Dynamically infers state types (not hard-coded)
- Detects unexpected states (popups, dialogs, errors)
- Identifies key elements that define the state
- Uses LLM reasoning, not keyword matching

**Reference:** [LLM-Powered GUI Agents](https://openreview.net/pdf/97488882edf61aec1f9d42514b1344eeb3a94e13.pdf)

---

### 2. Action Verifier Agent (`action_verifier_agent.py`)

**Purpose:** Verify proposed actions BEFORE execution using three boolean checks

**Input:**
- `proposed_action`: Action to verify (e.g., "click at (100, 200)")
- `screen_state`: Current screen state classification
- `task_goal`: Current user goal
- `elements`: Available UI elements

**Output:**
```json
{
    "approved": true/false,
    "is_safe": true/false,
    "is_relevant": true/false,
    "is_executable": true/false,
    "failed_checks": ["check1", "check2"],
    "reason": "brief explanation",
    "alternative_action": "suggested alternative (optional)"
}
```

**Three Boolean Checks:**
1. **is_safe**: Will this action cause harm? (YES/NO)
   - NO if: deletes data, makes purchases, grants dangerous permissions
2. **is_relevant**: Does this action move toward the task goal? (YES/NO)
   - NO if: goes wrong direction, opens unrelated features
3. **is_executable**: Can this action be performed on current screen? (YES/NO)
   - NO if: element doesn't exist, coordinates out of bounds

**Approval Logic:** ALL three checks must be YES for approval

**Reference:** [V-Droid Verifier-Driven Approach](https://arxiv.org/html/2503.15937v4)

---

### 3. Failure Diagnosis Agent (`failure_diagnosis_agent.py`)

**Purpose:** Diagnose failures and suggest specific recovery strategies

**Input:**
- `action`: The action that failed
- `state_before`: Screen state before action
- `state_after`: Screen state after action (if available)
- `error`: Error message (if any)
- `task_goal`: Current user goal

**Output:**
```json
{
    "failure_type": "PLANNING_ERROR|PERCEPTION_ERROR|ENVIRONMENT_ERROR|EXECUTION_ERROR",
    "root_cause": "brief description",
    "recovery_strategy": "specific recovery action",
    "should_retry": true/false,
    "retry_count_limit": 2,
    "reasoning": "explanation"
}
```

**Failure Taxonomy:**

1. **PLANNING_ERROR** - Wrong action chosen for current state
   - Recovery: "Press BACK, re-classify screen, try different approach"

2. **PERCEPTION_ERROR** - Failed to parse/understand screen elements
   - Recovery: "Re-call list_elements_on_screen, wait for loading"

3. **ENVIRONMENT_ERROR** - App crash, network issue, OS dialog
   - Recovery: "Dismiss dialog, restart app, check network"

4. **EXECUTION_ERROR** - Action failed to execute
   - Recovery: "Verify element exists, adjust coordinates, retry once"

**Reference:** [Failure Taxonomy Research](https://arxiv.org/html/2508.13143v1)

---

## Usage in Device Testing Agent

The main device testing agent can hand off to these sub-agents during autonomous navigation:

```python
# OBSERVE: Get screen state
elements = list_elements_on_screen(device_id)
screen_state = handoff_to(Screen State Classifier)  # Optional

# ACT: Propose and verify action
proposed_action = "click at (100, 200)"
verification = handoff_to(Action Verifier)  # Optional
if not verification.approved:
    # Try alternative action
    pass

# Execute action
execute(proposed_action)

# REFLECT: Check result and handle failures
new_elements = list_elements_on_screen(device_id)
if action_failed:
    diagnosis = handoff_to(Failure Diagnosis Specialist)
    execute_recovery(diagnosis.recovery_strategy)
```

## When to Use Sub-Agents

**Screen State Classifier:**
- Complex screen states that need structured analysis
- Detecting unexpected popups/dialogs
- Understanding multi-element screens

**Action Verifier:**
- Before executing critical actions
- When safety is a concern
- To ensure action relevance to goal

**Failure Diagnosis Specialist:**
- When an action fails or produces unexpected results
- To get structured recovery guidance
- To avoid infinite retry loops

**Note:** Sub-agents are **OPTIONAL** - use them when you need structured analysis, not for every single action.

## Design Principles

1. **NO Hard-Coded Heuristics**
   - All logic is LLM-based and dynamically inferred
   - No keyword matching (e.g., "if 'allow' in text then...")
   - No hard-coded state machines

2. **Boolean Decisions Only**
   - No arbitrary numerical scores (0.0-1.0)
   - All checks are YES/NO binary decisions
   - Clear approval/rejection logic

3. **Minimal Code Changes**
   - Integrates with existing OpenAI Agents SDK Runner
   - Uses handoff pattern for sub-agent delegation
   - No changes to core navigation tools

4. **LLM-Powered Reasoning**
   - All decisions made by LLM agents
   - Structured output formats (JSON)
   - Low temperature for consistent results

## Research References

- **V-Droid (Verifier-Driven):** https://arxiv.org/html/2503.15937v4
- **LLM GUI Agents:** https://openreview.net/pdf/97488882edf61aec1f9d42514b1344eeb3a94e13.pdf
- **Failure Taxonomy:** https://arxiv.org/html/2508.13143v1
- **Mobile-Agent-v2:** https://proceedings.neurips.cc/paper_files/paper/2024/file/0520537ba799d375b8ff5523295c337a-Paper-Conference.pdf

## Model Configuration

All sub-agents use:
- **Model:** `gpt-5-mini` (fast, cost-effective)
- **Tool Choice:** `none` (pure LLM reasoning, no tool calls)
- **Temperature:** 0.1-0.2 (low for consistent decisions)
- **Parallel Tool Calls:** `false` (sequential reasoning)

## Future Enhancements

Potential improvements (deferred to later iterations):
- App-specific knowledge bases
- Multi-turn dialogue with sub-agents
- Evaluation harnesses and benchmarks
- Vision-based screen analysis integration
- Learning from past failures

