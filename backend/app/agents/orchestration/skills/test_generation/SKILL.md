# Test Generation Skill

**Loaded at:** Level 2 (when task matches skill triggers)

## Overview

This skill enables automated test case generation from product requirements. The agent parses PRDs, extracts user stories, and generates golden bugs with comprehensive verification criteria.

## Capabilities

### 1. PRD Parsing
- Extract user stories from markdown PRDs
- Identify feature requirements
- Parse acceptance criteria
- Detect edge cases and error scenarios

### 2. Test Case Generation
- Generate step-by-step test actions
- Create expected outcomes for each step
- Assign difficulty and priority levels
- Categorize tests (smoke, regression, edge case)

### 3. Golden Bug Creation
- Format tests as reproducible bug reports
- Include device configuration requirements
- Define exact reproduction steps
- Specify verification checkpoints

## Model Usage

| Task | Model | Rationale |
|------|-------|-----------|
| Test generation | `gpt-5.4` | Complex reasoning required |
| Quality evaluation | `gpt-5-mini` | Verify test quality |
| PRD extraction | `gpt-5-nano` | Extract from large docs |

## Generation Templates

### User Story → Test Case
```yaml
input:
  user_story: "As a user, I want to set an alarm so that I wake up on time"
  
output:
  test_id: "alarm_set_basic"
  category: "data_entry"
  difficulty: "easy"
  steps:
    - action: "launch_app"
      package: "com.android.deskclock"
    - action: "tap"
      target: "Alarm tab"
    - action: "tap"
      target: "Add alarm button"
    - action: "set_time"
      value: "07:00"
    - action: "tap"
      target: "Save/OK button"
  expected_outcome:
    condition: "element_present"
    target: "Alarm for 7:00 AM"
```

### Feature Criteria Evaluation

When evaluating generated tests, verify:
1. **Coverage**: All user stories have at least one test
2. **Completeness**: Each test has clear steps and expected outcomes
3. **Reproducibility**: Steps are specific and unambiguous
4. **Categories**: Tests are properly categorized
5. **Priority**: Priority matches business criticality

## Inline LLM Evaluation

The orchestration session invokes LLM evaluation after test generation:

```python
# Feature criteria check
await evaluator.evaluate_test_case(
    test_case=generated_test,
    criteria=feature_criteria,
    categories=["smoke", "regression"]
)

# Device config verification
await verifier.verify_device_config(
    config=device_config,
    expected=exact_config_from_prd
)
```

## Workaround Detection

❌ **Not allowed:**
- Skipping difficult reproduction steps
- Using alternative navigation paths
- Changing device configuration to make test easier
- Combining or omitting test cases

✅ **Required:**
- Follow exact reproduction steps from PRD
- Use specified device configuration
- Generate all required test categories
- Include edge cases and error scenarios

