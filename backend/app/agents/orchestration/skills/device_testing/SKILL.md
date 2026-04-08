# Device Testing Skill

**Loaded at:** Level 2 (when task matches skill triggers)

## Overview

This skill enables autonomous mobile device testing through the Mobile MCP client. The agent can navigate apps, interact with elements, capture screenshots, and verify expected states.

## Capabilities

### 1. Mobile Navigation
- Launch apps by package name
- Navigate between screens
- Handle back navigation
- Scroll and swipe gestures

### 2. Element Interaction
- Tap on elements by coordinates or description
- Long press for context menus
- Text input and form filling
- Checkbox and radio button selection

### 3. Screen Analysis
- Screenshot capture and analysis
- Element detection and labeling
- State verification
- Visual regression detection

### 4. State Verification
- Check element presence/absence
- Verify text content
- Validate UI state
- Confirm navigation success

## Model Usage

| Task | Model | Rationale |
|------|-------|-----------|
| Screen analysis | `gpt-5-mini` | Vision + reasoning |
| Multi-step planning | `gpt-5.4` | Complex orchestration |
| MCP tool extraction | `gpt-5-nano` | Info extraction only |

## Action Templates

### Launch App
```json
{
  "action": "launch_app",
  "package": "{package_name}",
  "wait_for": "main_activity"
}
```

### Tap Element
```json
{
  "action": "tap",
  "target": "{element_description}",
  "coordinates": {"x": 0, "y": 0}
}
```

### Verify State
```json
{
  "action": "verify",
  "condition": "element_present",
  "target": "{expected_element}",
  "timeout_ms": 5000
}
```

## Error Handling

1. **Element not found**: Retry with screen refresh, then escalate
2. **App crash**: Relaunch app, reset to known state
3. **Timeout**: Increase timeout, check network state
4. **Unexpected screen**: Navigate back, retry navigation

## Linked Resources

- `templates/common_actions.yaml` - Standard action templates
- `templates/verification_patterns.yaml` - State verification patterns
- `../bug_reproduction/SKILL.md` - Related bug reproduction skill

