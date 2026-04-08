/**
 * Known issues database — every bug we've found and how we fixed it.
 */

export interface KnownIssue {
  id: string;
  title: string;
  category: string;
  severity: 'critical' | 'high' | 'medium' | 'low';
  symptom: string;
  rootCause: string;
  fix: string;
  file: string;
  commit?: string;
}

export const KNOWN_ISSUES: KnownIssue[] = [
  {
    id: 'bbox-coordinate-misalignment',
    title: 'Bounding Box Coordinate Misalignment',
    category: 'bbox_alignment',
    severity: 'critical',
    symptom: 'Bounding boxes drawn at completely wrong positions — labels and rectangles dont match actual UI elements. Elements appear off-screen.',
    rootCause: 'Mobile MCP take_screenshot returns JPEG at ~45% of native resolution (486×1080) but list_elements_on_screen returns coordinates in native device resolution (1080×2400). Drawing at native coords on scaled image puts everything ~2.2x off-position. The 45% scaling is a hardcoded limit in Mobile MCP version 2024-11-05.',
    fix: 'Get device screen size via get_screen_size(). Parse width/height using re.search(r"(\\d+)\\s*x\\s*(\\d+)"). Compute scale_x = img.width/screen_width and scale_y = img.height/screen_height. Apply these factors to every (x, y, w, h) before sending to PIL ImageDraw.',
    file: 'agents/device_testing/tools/autonomous_navigation_tools.py',
    commit: 'c7e5a68',
  },
  {
    id: 'missing-asyncio-import',
    title: 'Missing asyncio Import',
    category: 'import_errors',
    severity: 'critical',
    symptom: 'NameError: asyncio is not defined at line 552 (asyncio.to_thread call)',
    rootCause: 'asyncio.to_thread() was introduced for PIL drawing off-loading but the asyncio module was never imported.',
    fix: 'Added "import asyncio" at the module top level. Also verified that typing.Any and typing.Dict were present for the function signature.',
    file: 'agents/device_testing/tools/autonomous_navigation_tools.py',
    commit: 'a3773ee',
  },
  {
    id: 'async-def-in-to-thread',
    title: 'async def passed to asyncio.to_thread()',
    category: 'agent_chat',
    severity: 'critical',
    symptom: 'Bounding box drawing returns a coroutine object instead of results. SoM annotations silently fail.',
    rootCause: '_draw_bounding_boxes_threaded was incorrectly defined as "async def". asyncio.to_thread() runs the function in a separate thread and expects a standard synchronous function; passing a coroutine function causes it to return the coroutine itself without executing it.',
    fix: 'Removed the "async" keyword from _draw_bounding_boxes_threaded. The function now executes synchronously in its own thread as intended.',
    file: 'agents/device_testing/tools/autonomous_navigation_tools.py',
    commit: 'a3773ee',
  },
  {
    id: 'keyword-only-arg-mismatch',
    title: 'Keyword-Only Argument Mismatch',
    category: 'agent_chat',
    severity: 'high',
    symptom: 'TypeError on _bbox_find_label_position call — positional arguments passed to keyword-only parameters.',
    rootCause: 'Function internal signature used the * marker (internal_func(*, x, y, width, ...)) but the call site was passing them positionally: internal_func(x, y, ...).',
    fix: 'Updated the call site to use explicit keyword arguments: _bbox_find_label_position(x=x, y=y, width=width, height=height, ...).',
    file: 'agents/device_testing/tools/autonomous_navigation_tools.py',
    commit: 'a3773ee',
  },
  {
    id: 'annotated-filepath-not-reset',
    title: 'Annotated filepath not reset on bbox failure',
    category: 'agent_chat',
    severity: 'high',
    symptom: 'FileNotFoundError when vision API tries to read annotated screenshot that doesnt exist.',
    rootCause: 'The state variable "annotated_filepath" was preemptively updated to the ".annotated.png" path. If the heavy drawing process failed, the variable remained pointed at the non-existent file.',
    fix: 'Wrapped the drawing logic in try-except. In the except block, we explicitly reset annotated_filepath = filepath (the raw screenshot path) and logged the error.',
    file: 'agents/device_testing/tools/autonomous_navigation_tools.py',
    commit: 'a3773ee',
  },
  {
    id: 'agent-duplicate-device-calls',
    title: 'Agent calls list_devices and start_navigation twice',
    category: 'agent_chat',
    severity: 'critical',
    symptom: 'Agent calls list_available_devices AND start_navigation_session simultaneously, asks user which device, then hangs.',
    rootCause: 'The LLM (GPT-5-mini) had parallel_tool_calls=True. Seeing the task "navigate to Youtube", it reasoned that it needed to list devices AND start a session, calling both in one turn. This race condition led to session initialization failures.',
    fix: 'Set parallel_tool_calls=False for the device testing specialist. navigation tools now execute strictly one-by-one. Added critical instructions to auto-select "emulator-5554" without prompting.',
    file: 'agents/device_testing/device_testing_agent.py',
    commit: 'a5fb8b4',
  },
  {
    id: 'figma-images-rate-limit',
    title: 'Figma Images API Rate Limit (Plan-Tier)',
    category: 'figma_api',
    severity: 'medium',
    symptom: '429 on Figma REST API with Retry-After: 396156 (4.6 days)',
    rootCause: 'The Figma "Images" endpoint has extremely aggressive rate limits on free/professional plans. High-volume frame extraction during flow analysis triggers 100-hour lockouts.',
    fix: 'Implemented a Computer Vision fallback in "scripts/figma_cv_overlay.py". It uses Playwright to capture the full Figma browser view and uses adaptive thresholding + connected component analysis to find frames manually, bypassing the API entirely.',
    file: 'scripts/figma_cv_overlay.py',
  },
  {
    id: 'openai-rate-limits',
    title: 'OpenAI 429 Rate Limit Errors',
    category: 'rate_limits',
    severity: 'medium',
    symptom: '429 errors from OpenAI API during heavy agent usage.',
    rootCause: 'Standard Android XML accessibility hierarchies are extremely verbose (5,000+ tokens). Repeatedly sending these in a feedback loop hits TPM limits.',
    fix: 'Integrated TOON (Token Optimized Object Notation). It filters only interactive and text-bearing elements and strips redundant "com.google.android..." package prefixes, reducing payload size by 65%.',
    file: 'agents/device_testing/tools/autonomous_navigation_tools.py',
  },
  {
    id: 'chef-annotation-json-parse',
    title: 'Chef Annotation Crashes (JSON.parse / Zod)',
    category: 'chef_annotations',
    severity: 'high',
    symptom: 'UI render crashes, silent annotation drops, or response.json() failures.',
    rootCause: 'Chef was calling response.json() multiple times during usage recording, and encodeModelAnnotation was missing fallbacks for "provider" and "toolCallId" fields.',
    fix: 'Updated "annotations.ts" to store JSON in a variable, added try-catch guards to all JSON.parse calls, and added null-coalescing fallbacks (?? "Unknown") to the encoder.',
    file: 'integrations/chef/app/lib/common/annotations.ts',
  },
];

export const ISSUE_CATEGORIES = [...new Set(KNOWN_ISSUES.map(i => i.category))];
