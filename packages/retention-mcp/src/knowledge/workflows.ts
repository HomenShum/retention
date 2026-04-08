/**
 * Step-by-step workflows for common retention.sh tasks.
 */

export const WORKFLOWS: Record<string, string> = {
   bug_fix: `# Bug Fix Workflow

1. DIAGNOSE — Read error logs, reproduce the issue
   cd backend && tail -f /tmp/backend.log
2. LOCATE — Find the affected file using codebase search
3. ROOT CAUSE — Identify WHY, not just WHERE
4. FIX — Make minimal, targeted change
5. VERIFY — Run tests
   cd backend && pytest --tb=short
   cd frontend/test-studio && npm run build
6. TEST E2E — If UI-related, run E2E
   npx playwright test
7. COMMIT — Only when all checks pass
   git add <files> && git commit -m "fix: <description>"`,

   navigation_test: `# Navigation Test Workflow

1. ENSURE EMULATOR — adb devices (verify emulator-5554 connected)
2. START BACKEND — cd backend && python -m uvicorn app.main:app --reload --port 8000
3. START FRONTEND — cd frontend/test-studio && npm run dev
4. OPEN DEMO — http://localhost:5173/demo
5. SEND TASK — Type navigation task in chat (e.g. "go youtube find video for terry tricks")
6. OBSERVE — Watch agent execute OAVR loop:
   - Screenshots appear in emulator viewer
   - SoM annotations show bounding boxes
   - Agent narrates each step
7. VERIFY ANNOTATIONS — Check bounding boxes align with actual elements
   - If misaligned, check coordinate scaling (see: coordinate_scaling methodology)
8. CHECK COMPLETION — Agent should report task complete with evidence`,

   bbox_verify: `# Bounding Box Verification Workflow

1. CAPTURE — Take screenshot via agent or manually:
   ls -la backend/screenshots/emulator-5554_*_annotated.png
2. COMPARE — Open raw and annotated screenshots side-by-side
3. CHECK ALIGNMENT — Each bounding box should:
   - Surround the correct UI element
   - Have the right color for element type (see SoM palette)
   - Label matches element text/type
4. IF MISALIGNED:
   a. Check device screen size: adb shell wm size → e.g. 1080x2400
   b. Check screenshot size: python3 -c "from PIL import Image; print(Image.open('screenshot.png').size)"
   c. Verify scale factors: scale_x = img_width / screen_width
   d. Check autonomous_navigation_tools.py lines 397-448 for scaling code
5. TEST FIX — Run navigation task and verify annotations`,

   agent_debug: `# Agent Chat Debugging Workflow

1. CHECK SERVICES:
   curl http://localhost:8000/health
   curl http://localhost:5173
2. CHECK BACKEND LOGS:
   tail -f /tmp/backend.log
3. COMMON ISSUES:
   - Agent hangs → Check parallel_tool_calls setting (should be False for device testing)
   - Device not found → adb devices, check emulator running
   - Screenshot fails → Check Mobile MCP process running
   - Bbox wrong → Check coordinate scaling (methodology: coordinate_scaling)
4. SSE STREAM DEBUG:
   - Open browser DevTools → Network → filter "EventStream"
   - Check for proper event format: data: {...}
5. AGENT TRACE — Check LangSmith (if configured):
   https://smith.langchain.com`,

   feature: `# Feature Development Workflow

1. PLAN — Define scope, affected files, architecture impact
2. BRANCH — git checkout -b feature/<name>
3. IMPLEMENT — Follow code style (Python: type hints + docstrings, TS: explicit types)
4. TEST — Write unit tests alongside implementation
5. VERIFY BACKEND:
   cd backend && pytest --tb=short
6. VERIFY FRONTEND:
   cd frontend/test-studio && npm run build && npm run lint
7. E2E — Test end-to-end if UI-related
   npx playwright test
8. REVIEW — Self-review diff before commit
9. COMMIT — Descriptive commit message
10. PUSH — git push origin feature/<name>`,

   figma_analysis: `# Figma Flow Analysis Workflow

1. GET API KEY — Figma Personal Access Token
2. EXTRACT — Figma REST API (depth=3): DOC→CANVAS→SECTION→FRAME
3. CLUSTER — Multi-signal priority cascade:
   Sections → Prototype connections → Name prefixes → Spatial (Y-bin + X-gap)
4. VISUALIZE — PIL bounding boxes on Figma canvas screenshots
5. IF RATE-LIMITED — Use CV overlay (no API calls):
   cd backend && python scripts/figma_cv_overlay.py
   - Brightness thresholding (>80 for sections, >100 for frames)
   - Morphological closing/opening (scipy.ndimage)
   - Connected component analysis
   
Key files:
- app/figma/flow_analyzer.py — Core pipeline
- scripts/figma_cv_overlay.py — CV fallback`,

   flicker_test: `# Flicker Detection Test Workflow

1. ENSURE EMULATOR — adb devices
2. START RECORDING — Layer 1 triggers adb screenrecord (60fps)
3. REPRODUCE — Navigate to the screen with suspected flicker
4. EXTRACT FRAMES — Layer 2 uses ffmpeg scene detection
5. ANALYZE — Parallel SSIM comparison of consecutive frames
6. VERIFY — Layer 3 sends suspicious frames to GPT-5.4 vision
7. REPORT — Classified as bug (flicker/glitch) or animation (expected)

Quick test:
cd backend && python scripts/test_flicker_detection.py`,

   verify_before_commit: `# Closed-Loop Verification Workflow (Ralph Loop)

1. CODE — Implement the fix or feature
2. LINT — cd backend && python -m mypy app/
3. UNIT TEST — cd backend && pytest app/tests/test_failing_module.py
4. CHECK IMPORTS — Ensure all new modules are imported in __init__.py
5. CHECK ASYNC — Verify no 'async' functions passed to asyncio.to_thread()
6. VERIFY HUD — Open frontend demo, trigger the agent, watch the stream
7. COMMIT — Only after 100% green tests and successful HUD observation`,
};

export const WORKFLOW_LIST = Object.keys(WORKFLOWS);

export const QUICK_COMMANDS = `# Quick Commands

## Backend
cd backend && python -m uvicorn app.main:app --reload --port 8000
cd backend && pytest --tb=short
cd backend && python -m mypy app/ --ignore-missing-imports

## Frontend
cd frontend/test-studio && npm run dev
cd frontend/test-studio && npm run build
cd frontend/test-studio && npm run lint
cd frontend/test-studio && npx tsc --noEmit

## E2E
npx playwright test
npx playwright test --trace on
npx playwright show-report

## Device
adb devices
adb shell wm size
adb shell screencap -p /sdcard/screen.png && adb pull /sdcard/screen.png

## Git
git add <files> && git diff --cached --stat
git commit -m "fix: description"
git push origin main`;

