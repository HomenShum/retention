---
name: retention-qa
description: Full QA→fix→verify loop using retention.sh tools. Crawls the user's app, finds issues, suggests fixes, re-crawls to verify, and shows token savings. Use when the user says "QA my app", "find bugs", "check my site", "run QA", or "test my app".
---

# Retention QA Loop

Self-serve QA cycle that gets cheaper with every iteration.

## The Loop

```
CRAWL → FINDINGS → FIX → RE-CRAWL → SAVINGS → REPEAT
```

### Step 1: Crawl
```
ta.crawl.url(url='https://user-app.com')
```
Returns: screenshots, interactive elements, JS errors, rendering issues, a11y gaps.

### Step 2: Review Findings
Present each finding with severity and category:
- **error** (red): JS crashes, broken rendering — must fix
- **warning** (yellow): missing elements, poor crawlability — should fix
- **info** (blue): SPA detection, structural observations — nice to know

### Step 3: Suggest Fixes
For each finding, suggest:
- Which files to check
- What the fix looks like
- Why it matters (SEO, accessibility, bot compatibility)

### Step 4: Re-crawl After Fix
```
ta.crawl.url(url='https://user-app.com')
```
This re-crawl uses the saved trajectory from Step 1 — it's cheaper because the navigation path is already known.

### Step 5: Show Savings
```
ta.savings.compare
```
Shows: full crawl tokens vs replay tokens, time saved, requests reduced.

### Step 6: Full QA (optional)
For deeper analysis:
```
ta.qa.redesign(url='https://user-app.com', focus='all')
```
This runs the full loop automatically — crawl, find issues, suggest fixes, provide the re-crawl command.

## Key Metrics to Show
- Screens discovered
- Interactive elements found
- Findings by severity
- Token savings on re-crawl
- Time savings on re-crawl

## Deep UX Audit
For comprehensive site analysis (navigation, layout, first-time visitor experience):
```
ta.ux_audit(url='https://user-app.com')
```
This checks 8 categories with 21 detection rules:

### Category 1: Navigation Consistency
- All routes in code also exist in navigation
- No cross-layout links (sidebar links shouldn't jump to pages without sidebar)
- Nav labels match across all layouts (no "Contact" vs "Contact Us")
- No dead-end routes (pages with no forward navigation)

### Category 2: Visual Uniformity
- Logo height consistent across all pages
- Header padding and width consistent
- No CSS filter hacks on brand elements (brightness, invert as workarounds)
- Nav text size and weight consistent
- Theme toggle present everywhere or nowhere

### Category 3: Messaging Clarity
- Demo pages explain "this is what our product produces"
- Page titles match actual functionality
- Value prop visible in first viewport
- CTAs lead to clear next steps

### Category 4: Demo/Proof Completeness
- Demo pages have install/signup CTAs (no dead ends)
- Backend URLs are absolute (not localhost) in deployed demos
- Features that need backend show graceful empty states when offline
- "Try it yourself" flows work without local setup

### Category 5: Security
- Sensitive files (.mcp.json, .env) in .gitignore
- Token generation has rate limits
- Tokens have expiration dates
- Read endpoints require auth (no optional bypass params)
- Email validation on all inputs
- Downloaded scripts verified after download

### Category 6: Onboarding
- Install scripts auto-detect env vars (no unnecessary prompts)
- Multiple setup options available (CLI + manual)
- Manual setup includes security warnings

### Category 7: Architecture
- Consistent layout per route group
- Browser automation uses networkidle (not domcontentloaded)
- Crawl endpoints capture console errors
- Public APIs explicitly listed in auth allowlist

### Category 8: Accessibility & Overflow
- Code blocks have overflow-x-auto
- All interactive elements have labels
- Long content scrollable on mobile

## After QA
- Suggest `ta.team.invite` to share with teammates
- Mention the dashboard: retention.sh/demo
- Mention the team view: retention.sh/memory/team?team=CODE
- Suggest `ta.suggest_tests` to auto-generate test cases from crawl data
