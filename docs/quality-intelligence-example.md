# retention.sh — Quality Intelligence Example

> This is the deeper wedge. Not just "button click failed" but structural UX findings.

---

## The Difference

### What raw QA tools tell you:

```
FAIL: Login form submission
  Expected: validation error shown
  Actual: form submitted without validation
```

### What retention.sh Quality Intelligence tells you:

```json
{
  "verdict": "fail",
  "failure_step": "Submit login form with invalid email 'notanemail'",

  "quality_intelligence": {
    "structural_findings": [
      {
        "type": "layout_crowding",
        "severity": "high",
        "element": "Login form",
        "detail": "6 input fields visible above the fold on a 375px viewport. Users see: email, password, first name, last name, phone, company. Only email+password are required for login.",
        "recommendation": "Move non-essential fields to a separate registration step. Login should show only email + password.",
        "impact": "Increases form abandonment. Users mistake login for registration."
      },
      {
        "type": "visual_hierarchy_broken",
        "severity": "high",
        "element": "Submit button vs social login buttons",
        "detail": "Primary 'Sign In' button (14px, gray border) is visually smaller and less prominent than 'Continue with Google' button (16px, blue fill). Users will click Google first even when they have an account.",
        "recommendation": "Make primary submit button the most visually dominant element. Social logins should be secondary.",
        "impact": "Misdirects user attention. Primary action is not the visual default."
      },
      {
        "type": "dead_element",
        "severity": "medium",
        "element": "'Remember me' checkbox",
        "detail": "Checkbox renders and accepts clicks but localStorage/cookie check shows no session persistence implemented. Clicking it does nothing.",
        "recommendation": "Either implement session persistence or remove the checkbox. Broken affordances erode trust.",
        "impact": "Users expect session persistence after checking. When they return and must re-login, they blame the app."
      },
      {
        "type": "state_transition_confusion",
        "severity": "medium",
        "element": "Form submission feedback",
        "detail": "After clicking submit with invalid email, no visible state change occurs for 800ms. Then the page refreshes. Users cannot tell if their click registered or if the form is processing.",
        "recommendation": "Show immediate feedback: disable button, show spinner, then display inline validation error.",
        "impact": "Users double-click, triggering duplicate submissions."
      },
      {
        "type": "interaction_depth_excessive",
        "severity": "low",
        "element": "Password requirements tooltip",
        "detail": "Password rules are shown in a tooltip that appears on hover (not on focus). Mobile users cannot discover the rules until after submission fails.",
        "recommendation": "Show password requirements inline below the field, visible on focus.",
        "impact": "Mobile users fail password validation 2-3 times before discovering requirements."
      }
    ],

    "ux_friction_score": 0.72,
    "interaction_depth": 4,
    "cognitive_load_estimate": "high",

    "summary": "This login form has 5 structural issues beyond the validation bug. The form is overcrowded (6 fields for a 2-field task), the primary action is visually subordinate to social login, the 'Remember me' checkbox is non-functional, submission feedback is delayed, and password rules are hidden from mobile users. Fixing the validation bug alone will not make this form usable."
  }
}
```

---

## Why This Matters

Raw QA tells the coding agent: **"Fix the email validation."**

Quality Intelligence tells the coding agent AND the PM/designer:

1. **The form has too many fields** — move non-essential ones to registration
2. **The button hierarchy is wrong** — primary action should be visually dominant
3. **A UI element is broken** — 'Remember me' does nothing
4. **Feedback is too slow** — users think the form didn't register their click
5. **Mobile users can't see password rules** — tooltip doesn't work on touch

The coding agent can fix #1, #3, #4, and #5. The designer needs to fix #2. The PM needs to decide whether to keep or cut 'Remember me'.

**This is the layer that makes retention.sh more than a test runner.**

---

## Finding Types Reference

| Type | What It Means | Who Fixes It |
|------|--------------|-------------|
| `layout_crowding` | Too many elements competing for attention in a viewport | Designer / PM |
| `visual_hierarchy_broken` | Primary action is not the most visually prominent element | Designer |
| `dead_element` | Element renders but has no functional backend/handler | Engineer |
| `ornamental_element` | Element adds visual weight but no user value | PM / Designer |
| `state_transition_confusion` | User cannot tell what happened after an interaction | Engineer |
| `interaction_depth_excessive` | Too many steps to complete a simple task | PM |
| `cognitive_load_high` | Too much information / too many choices on one view | Designer / PM |
| `repeated_friction` | Same UX issue appears across multiple flows | PM (systemic) |

---

## How It Gets Generated

1. **retention.sh runs the flow** and captures before/after screenshots, DOM snapshots, and interaction traces
2. **LLM-as-judge** analyzes the captured evidence with a structured prompt:
   - "Given this screenshot and DOM, identify structural UX issues beyond pass/fail"
   - Uses a fixed taxonomy (the 8 types above)
   - Each finding requires: type, severity, element, detail, recommendation, impact
3. **Friction score** is computed from finding count and severity weights
4. **Results are appended** to the evidence schema under `quality_intelligence`

---

## Example Prompt for LLM Judge (Quality Layer)

```
You are a UX quality analyst reviewing a web application screen.

Given:
- Screenshot (before interaction): {before.png}
- Screenshot (after interaction): {after.png}
- DOM snapshot: {dom_snapshot}
- Task: {user_task_description}
- Verdict: {pass_or_fail}

Identify structural UX issues using ONLY these types:
- layout_crowding
- visual_hierarchy_broken
- dead_element
- ornamental_element
- state_transition_confusion
- interaction_depth_excessive
- cognitive_load_high
- repeated_friction

For each finding, provide:
- type (from list above)
- severity (high/medium/low)
- element (which specific UI element)
- detail (what is wrong, with measurements if visible)
- recommendation (specific fix)
- impact (what happens to users because of this)

Return JSON array. Omit types with no findings. Be specific — reference exact elements, sizes, colors, positions.
```

---

## Integration With Fix Loop

When quality intelligence findings are present, the compact failure bundle includes them. Claude Code can then:

1. **Fix the test failure** (validation bug) — immediate
2. **Fix dead elements** (remove broken checkbox or implement handler) — same PR
3. **Flag layout/hierarchy issues** for designer — create GitHub issue
4. **Report friction score** — "this page scored 0.72 friction, above 0.5 threshold"

This turns retention.sh from a test runner into a **quality advisor**.
