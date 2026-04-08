# retention-mcp 🚀

**The definitive domain knowledge layer for AI agents building mobile test automation at retention.sh.**

AI agents often struggle with project-specific context, unique navigation patterns, and "tribal knowledge" about past bugs. `retention-mcp` solves this by giving your agent (Claude, Cursor, Windsurf) structured, programmatic access to the team's methodologies, codebase maps, and verified bug fixes.

---

## 📋 Prerequisites

- **Node.js**: `v18.0.0` or higher
- **MCP Client**: An IDE or tool that supports the [Model Context Protocol](https://modelcontextprotocol.io) (e.g., Claude Desktop, Cursor, Windsurf, VS Code)

---

## ⚡ Quick Start

```bash
npx retention-mcp
```

---

## 📦 Installation & Setup

### Claude Desktop / Claude Code
```bash
claude mcp add retention -- npx -y retention-mcp@latest
```

### Cursor
Add to `.cursor/mcp.json`:
```json
{
  "mcpServers": {
    "retention": {
      "command": "npx",
      "args": ["-y", "retention-mcp@latest"]
    }
  }
}
```

### Windsurf
Add to `~/.codeium/windsurf/mcp_config.json`:
```json
{
  "mcpServers": {
    "retention": {
      "command": "npx",
      "args": ["-y", "retention-mcp@latest"]
    }
  }
}
```

### VS Code (Copilot)
Add to `.vscode/mcp.json`:
```json
{
  "servers": {
    "retention": {
      "command": "npx",
      "args": ["-y", "retention-mcp@latest"]
    }
  }
}
```

---

## 🛠️ MCP Tools (7 Total)

| Tool | Description |
|------|-------------|
| `getMethodology` | Returns deep implementation docs for 15 topics: OAVR, SoM annotation, coordinate scaling, flicker detection, golden bugs, failure diagnosis, model tiering, mobile MCP fallback, simulation lifecycle, subagent handoff, boolean verification, HUD streaming, agent config, vision click, self-correction |
| `getKnownIssues` | Returns all 9+ discovered bugs with symptom, root cause, fix, file, and commit SHA |
| `getCodebaseMap` | Returns full codebase structure with file purposes across 7 sections |
| `getWorkflow` | Returns step-by-step workflows: bug_fix, navigation_test, bbox_verify, agent_debug, feature, figma_analysis, flicker_test, verify_before_commit |
| `getQuickCommands` | Returns all dev commands (backend, frontend, E2E, device, git) |
| `getConventions` | Returns code style guidelines + 7 critical implementation rules |
| `getAgentConfig` | Returns full agent configuration reference (models, parallel_tool_calls, reasoning, streaming) |

---

## 🎨 Figma Flow Analysis — Complete Implementation

### Architecture: 3-Phase Pipeline

```
Phase 1: EXTRACT          Phase 2: CLUSTER              Phase 3: VISUALIZE
┌──────────────┐    ┌─────────────────────┐    ┌──────────────────────┐
│ Figma REST   │    │ Multi-Signal        │    │ PIL Bounding Boxes   │
│ API depth=3  │───▶│ Priority Cascade    │───▶│ on Canvas Screenshot │
│              │    │                     │    │                      │
│ DOC→CANVAS→  │    │ 1. Section-based    │    │ Color per flow group │
│ SECTION→FRAME│    │ 2. Prototype links  │    │ Labels + Semi-trans. │
│              │    │ 3. Name-prefix      │    │ fill + group names   │
│              │    │ 4. Spatial (Y+X gap)│    │                      │
└──────────────┘    └─────────────────────┘    └──────────────────────┘
```

**Key file**: `backend/app/figma/flow_analyzer.py` (707 lines)

### Data Structures

```python
@dataclass
class FigmaFrame:
    """A single frame/screen extracted from a Figma page."""
    node_id: str          # Figma node ID (e.g., "1234:5678")
    name: str             # Frame name from Figma (e.g., "Login / Screen 1")
    x: float              # absoluteBoundingBox.x
    y: float              # absoluteBoundingBox.y
    width: float          # absoluteBoundingBox.width
    height: float         # absoluteBoundingBox.height
    transition_targets: List[str]    # Prototype connection target node IDs
    section_name: Optional[str]      # Parent SECTION node name (if inside one)

@dataclass
class FlowGroup:
    """A cluster of frames that form a single visual flow."""
    group_id: int                    # 0-indexed group identifier
    name: str                        # Auto-generated group name
    frames: List[FigmaFrame]         # All frames in this flow
    color: Tuple[int, int, int]      # RGB color for visualization
    bbox_x: float                    # Bounding box of the entire group
    bbox_y: float
    bbox_w: float
    bbox_h: float
```

### Phase 1: Extract Frames (`extract_frames`)

**Critical**: Must use `depth=3` (not 2) — depth=2 only gets SECTION nodes, missing the FRAME nodes inside them.

```
Document tree traversal: DOC (depth=0) → CANVAS (depth=1) → SECTION (depth=2) → FRAME (depth=3)
```

```python
# Figma REST API call
GET https://api.figma.com/v1/files/{file_key}?depth=3

# Frame extraction logic
for canvas in document.children:           # CANVAS pages
    for child in canvas.children:          # SECTIONs or top-level FRAMEs
        if child.type == "SECTION":
            section_name = child.name
            for frame in child.children:   # FRAMEs inside SECTION
                if frame.type == "FRAME":
                    frames.append(FigmaFrame(
                        node_id=frame.id,
                        name=frame.name,
                        x=frame.absoluteBoundingBox.x,
                        y=frame.absoluteBoundingBox.y,
                        width=frame.absoluteBoundingBox.width,
                        height=frame.absoluteBoundingBox.height,
                        section_name=section_name,
                        transition_targets=extract_transitions(frame),
                    ))
        elif child.type == "FRAME":
            frames.append(...)             # Top-level frame (no section)
```

**Prototype connection extraction**: Recursively walks the node tree looking for `transitionNodeID` in reactions:
```python
def extract_transitions(node) -> List[str]:
    targets = []
    for reaction in node.get("reactions", []):
        action = reaction.get("action", {})
        if action.get("type") == "NODE" and action.get("transitionNodeID"):
            targets.append(action["transitionNodeID"])
    # Recurse into children
    for child in node.get("children", []):
        targets.extend(extract_transitions(child))
    return targets
```

### Phase 2: Multi-Signal Clustering (`cluster_flows`)

Uses a **priority cascade** — tries each signal in order, uses the first one that produces ≥2 groups:

#### Signal 1: Section-Based (Highest Priority)
If frames have `section_name` (from Figma SECTION nodes), group by section:
```python
groups = defaultdict(list)
for frame in frames:
    key = frame.section_name or "__no_section__"
    groups[key].append(frame)
# Each section becomes a flow group
```

#### Signal 2: Prototype Connections (Union-Find)
Build a graph from `transitionNodeID` links, find connected components:
```python
# Union-Find: frames connected by prototype links form one flow
parent = {f.node_id: f.node_id for f in frames}
for frame in frames:
    for target_id in frame.transition_targets:
        union(parent, frame.node_id, target_id)
# Each connected component = one flow group
```

#### Signal 3: Name-Prefix Matching
Parse frame names for shared prefixes (e.g., "Login / Screen 1", "Login / Screen 2"):
```python
# Split by common separators: " / ", " - ", " — "
prefix = frame.name.split(" / ")[0]  # "Login"
groups[prefix].append(frame)
# Minimum prefix length: 3 characters
```

#### Signal 4: Spatial Clustering (Lowest Priority)
Y-binning (horizontal rows) + X-gap splitting (vertical gaps between frames):
```python
# 1. Sort frames by Y coordinate
# 2. Bin into rows where Y-gap < threshold (default: 200px)
# 3. Within each row, sort by X and split where X-gap > threshold
# Result: visually proximate frames form flow groups
```

### Phase 3: Visualization (`visualize_flow_groups`)

Renders colored bounding boxes on the Figma canvas screenshot using PIL:

```python
FLOW_COLORS = [
    (255, 99, 71),   # Tomato
    (30, 144, 255),  # DodgerBlue
    (50, 205, 50),   # LimeGreen
    (255, 165, 0),   # Orange
    (148, 103, 189), # Purple
    (255, 20, 147),  # DeepPink
    # ... 12 distinct colors total
]

for group in flow_groups:
    color = FLOW_COLORS[group.group_id % len(FLOW_COLORS)]
    # Semi-transparent fill
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw_overlay = ImageDraw.Draw(overlay)
    draw_overlay.rectangle(
        [group.bbox_x, group.bbox_y,
         group.bbox_x + group.bbox_w, group.bbox_y + group.bbox_h],
        fill=(*color, 40),       # 40/255 alpha
        outline=(*color, 200),   # Strong outline
        width=3,
    )
    # Group label
    draw.text((group.bbox_x, group.bbox_y - 20),
              f"Flow {group.group_id}: {group.name}",
              fill=color, font=font)
```

### CV Overlay Fallback (No API Calls)

**Key file**: `backend/scripts/figma_cv_overlay.py` (162 lines)

When the Figma Images API is rate-limited (429 with `Retry-After: 396156` = 4.6 days), we bypass the API entirely using **pure computer vision** on browser screenshots:

```python
# 1. Capture Figma canvas via Playwright
page = await browser.new_page()
await page.goto(f"https://www.figma.com/file/{file_key}")
screenshot = await page.screenshot(full_page=True)

# 2. Brightness thresholding to find content regions
img_array = np.array(Image.open(screenshot).convert("L"))
brightness = img_array.astype(float)

# Section detection: brightness > 80
sec_mask = brightness > 80
sec_closed = binary_closing(sec_mask, structure=np.ones((7,7)), iterations=3)
sec_clean = binary_opening(sec_closed, structure=np.ones((5,5)), iterations=1)
labeled_sec, n_sec = ndimage.label(sec_clean)

# 3. Connected component analysis detects section groups
for i in range(1, n_sec + 1):
    region = (labeled_sec == i)
    ys, xs = np.where(region)
    bbox = (xs.min(), ys.min(), xs.max(), ys.max())
    section_bboxes.append(bbox)

# 4. Sub-frame detection via column brightness profiling
def detect_sub_frames(section_crop, min_gap=20):
    """Detect individual frames within a section using brightness columns."""
    col_brightness = section_crop.mean(axis=0)  # Average brightness per column
    # Find gaps (low brightness columns = separators between frames)
    gaps = np.where(col_brightness < threshold)[0]
    # Split at gaps wider than min_gap pixels
    frame_boundaries = split_at_gaps(gaps, min_gap)
    return frame_boundaries
```

### Figma API Gotchas

| Gotcha | Detail |
|--------|--------|
| `depth=3` required | depth=2 only gets SECTION nodes, not the FRAMEs inside them |
| Images API rate limit | Free/professional plans: 100-hour lockouts (`Retry-After: 396156`) |
| `absoluteBoundingBox` on SECTIONs | Includes padding — don't compute from child frames |
| Browser session API | `/api/files/:key` returns metadata only, not document tree (loaded via WASM) |
| Page filter | Filter by page name before processing to avoid cross-page frame mixing |

---

## 📱 Device Emulator — Complete Implementation

### Architecture: OAVR Pattern (Observe-Act-Verify-Reason)

```
┌─────────┐    ┌──────┐    ┌────────┐    ┌────────┐
│ OBSERVE  │───▶│ ACT  │───▶│ VERIFY │───▶│ REASON │
│          │    │      │    │        │    │        │
│ Screen   │    │ Click│    │ Action │    │ Failure│
│ Classifier│   │ Swipe│    │ Verifier│   │ Diagno-│
│ Agent    │    │ Type │    │ Agent  │    │ sis    │
│ (GPT-5-  │    │ etc. │    │ (GPT-5-│    │ Agent  │
│  mini)   │    │      │    │  mini) │    │        │
└─────────┘    └──────┘    └────────┘    └────────┘
     │                                        │
     └──── Loop until task complete ◄─────────┘
```

**Key file**: `backend/app/agents/device_testing/device_testing_agent.py` (950 lines)

#### Agent Configuration (Critical Settings)

```python
device_testing_agent = Agent(
    name="Device Testing Specialist",
    model="gpt-5-mini",                    # Vision-capable model
    parallel_tool_calls=False,              # ← CRITICAL: sequential execution
    model_settings=ModelSettings(
        reasoning=Reasoning(effort="medium"),  # Balance speed vs accuracy
    ),
    tools=[
        list_available_devices,
        take_screenshot,
        list_elements_on_screen,
        click_at_coordinates,
        type_text,
        swipe_on_screen,
        press_button,
        launch_app,
        vision_click,
        start_navigation_session,
        get_session_context,
        # ... 20+ tools total
    ],
)
```

**Why `parallel_tool_calls=False`**: Navigation is inherently sequential (observe → act → verify). With `parallel_tool_calls=True`, the LLM would call `list_available_devices` AND `start_navigation_session` simultaneously, causing race conditions and session initialization failures.

### SoM Screenshot Annotation System

**Key file**: `backend/app/agents/device_testing/tools/autonomous_navigation_tools.py` (1203 lines)

Based on OmniParser's **Set-of-Mark (SoM)** approach — color-coded, type-aware bounding boxes overlaid on screenshots.

#### 9-Color Element Type Palette

```python
ELEMENT_TYPE_COLORS = {
    "button":    "dodgerblue",    # BTN    — Button, ImageButton, FAB
    "input":     "orange",        # INPUT  — EditText, SearchView
    "toggle":    "purple",        # TOGGLE — Switch, CheckBox, RadioButton
    "nav":       "deeppink",      # NAV    — BottomNavigationView, Toolbar
    "image":     "darkcyan",      # IMG    — ImageView
    "text":      "gray",          # TXT    — TextView
    "list":      "forestgreen",   # LIST   — RecyclerView, ListView
    "container": "darkgray",      # BOX    — FrameLayout, LinearLayout
    "unknown":   "green",         # ELEM   — Unclassified elements
}

# Class name → type mapping (ORDERING MATTERS — first match wins)
CLASS_TO_TYPE = [
    ("radiobutton",    "toggle"),   # Must precede "button"
    ("compoundbutton", "toggle"),   # Must precede "button"
    ("checkbox",       "toggle"),
    ("switch",         "toggle"),
    ("button",         "button"),   # Generic button AFTER specific subclasses
    ("edittext",       "input"),
    ("searchview",     "input"),
    ("imageview",      "image"),
    ("textview",       "text"),
    ("recyclerview",   "list"),
    ("bottomnavigation", "nav"),
    ("toolbar",        "nav"),
    ("framelayout",    "container"),
    ("linearlayout",   "container"),
]
```

#### Coordinate Scaling Fix (Critical Bug Fix)

Mobile MCP returns screenshots at ~45% of native device resolution, but element coordinates are in native resolution space:

```
┌──────────────────────────────────────────────────────────┐
│ Device Screen:      1080 × 2400  (native resolution)     │
│ Screenshot Image:    486 × 1080  (Mobile MCP JPEG ~45%)  │
│ Element Coordinates: 1080 × 2400 (from list_elements)    │
│                                                           │
│ BUG: Drawing at native coords on 486×1080 image = 2.2x off│
│                                                           │
│ FIX: scale_x = 486/1080 = 0.45                           │
│      scale_y = 1080/2400 = 0.45                           │
│      scaled_x = native_x * scale_x                        │
└──────────────────────────────────────────────────────────┘
```

```python
def _draw_bounding_boxes_threaded(
    filepath: str,
    elements: List[Dict[str, Any]],
    screen_size: Optional[Tuple[int, int]] = None,
) -> str:
    """Draw SoM bounding boxes — runs in thread via asyncio.to_thread()."""
    img = Image.open(filepath)
    draw = ImageDraw.Draw(img)

    # Compute scale factors
    scale_x, scale_y = 1.0, 1.0
    if screen_size:
        screen_width, screen_height = screen_size
        scale_x = img.width / screen_width    # e.g., 486/1080 = 0.45
        scale_y = img.height / screen_height  # e.g., 1080/2400 = 0.45

    for elem in elements:
        # Get raw coordinates (native resolution)
        raw_x = elem.get("coordinates", {}).get("x", elem.get("x", 0))
        raw_y = elem.get("coordinates", {}).get("y", elem.get("y", 0))
        raw_w = elem.get("coordinates", {}).get("width", elem.get("width", 0))
        raw_h = elem.get("coordinates", {}).get("height", elem.get("height", 0))

        # Apply scaling
        x = int(raw_x * scale_x)
        y = int(raw_y * scale_y)
        width = int(raw_w * scale_x)
        height = int(raw_h * scale_y)

        # Classify element type
        class_name = elem.get("className", "").lower()
        elem_type = classify_element(class_name)
        color = ELEMENT_TYPE_COLORS[elem_type]
        tag = ELEMENT_TYPE_TAGS[elem_type]

        # Draw bounding box
        draw.rectangle([x, y, x + width, y + height], outline=color, width=2)
        # Draw label
        label = f"[{tag}] {elem.get('text', '')[:20]}"
        draw.text((x, y - 12), label, fill=color, font=font)

    annotated_path = filepath.replace(".png", "_annotated.png")
    img.save(annotated_path)
    return annotated_path

# Called from async context:
screen_size_str = await mobile_mcp_client.get_screen_size(device_id)
# Returns: "Screen size is 1080x2400 pixels"
match = re.search(r"(\d+)\s*x\s*(\d+)", screen_size_str)
screen_size = (int(match.group(1)), int(match.group(2)))
annotated = await asyncio.to_thread(
    _draw_bounding_boxes_threaded, filepath, elements, screen_size
)
```

### Vision Click (Agentic Vision — GPT-5.4)

**Key file**: `backend/app/agents/device_testing/agentic_vision_service.py` (847 lines)

When the accessibility tree (`list_elements_on_screen`) is insufficient — canvas-based UIs, loading states, or custom views — we use **GPT-5.4 with code execution** to find elements visually.

#### Two-Layer Architecture

```
Layer 1: SoM Structural Annotation (deterministic, <100ms, free)
  Accessibility tree → element classification → color-coded bounding boxes

Layer 2: GPT-5.4 Agentic Vision (intelligent, Think-Act-Observe)
  SoM-annotated image + element list → GPT-5.4 generates Python code →
  LocalCodeExecutor runs it → results fed back for next iteration
```

```python
async def vision_click(device_id: str, query: str, target: str) -> str:
    """Find and click an element using GPT-5.4 vision."""
    # 1. Take screenshot
    result = await mobile_mcp_client.take_screenshot(device_id)
    image_bytes = base64.b64decode(result["data"])

    # 2. Get screen size for coordinate mapping
    size_str = await mobile_mcp_client.get_screen_size(device_id)
    screen_w, screen_h = parse_screen_size(size_str)

    # 3. Call Agentic Vision (Think-Act-Observe loop)
    client = AgenticVisionClient()
    vision_result = await client.multi_step_vision(
        image_bytes, f"Find {target} and return COORDINATES: (x, y)"
    )

    # 4. Parse coordinates from GPT-5.4's final analysis
    match = re.search(
        r"COORDINATES:\s*\(?(\d+),\s*(\d+)\)?",
        vision_result.final_analysis
    )
    x, y = int(match.group(1)), int(match.group(2))

    # 5. Execute click at found coordinates
    await mobile_mcp_client.click_on_screen(device_id, x, y)
    return f"Clicked at ({x}, {y})"
```

#### Think-Act-Observe Loop (Agentic Vision)

```python
@dataclass
class AgenticVisionResult:
    final_analysis: str          # GPT-5.4's conclusion
    steps: List[VisionStep]      # All Think-Act-Observe steps
    total_steps: int
    images_generated: int
    success: bool

class AgenticVisionClient:
    async def multi_step_vision(self, image: bytes, query: str,
                                 max_steps: int = 5) -> AgenticVisionResult:
        """GPT-5.4 Think-Act-Observe loop with code execution."""
        for step in range(max_steps):
            # THINK: GPT-5.4 analyzes image + query
            response = await self.provider.generate(
                images=[current_image],
                prompt=f"Analyze this image. Query: {query}\n"
                       f"Generate Python code to investigate further."
            )
            # ACT: Execute generated code locally
            code = extract_code_block(response)
            result = self.executor.execute(code, {"image": current_image})
            # OBSERVE: Feed results back
            if result.has_image:
                current_image = result.image  # Zoomed/cropped/annotated
            if "COORDINATES:" in result.output:
                return AgenticVisionResult(final_analysis=result.output, ...)
```

### Mobile MCP Integration + ADB Fallback

**Key file**: `backend/app/agents/device_testing/mobile_mcp_client.py` (1124 lines)

Every Mobile MCP operation has a comprehensive ADB fallback:

```python
class MobileMCPClient:
    async def take_screenshot(self, device: str) -> Dict[str, Any]:
        """Take screenshot — Mobile MCP with ADB fallback."""
        try:
            # Primary: Mobile MCP (returns JPEG at ~45% resolution)
            result = await self._call_mcp_tool(
                "mobile_take_screenshot_mobile-mcp",
                {"device": device}
            )
            return {"type": "image", "data": result["data"],
                    "mimeType": "image/jpeg"}
        except Exception:
            # Fallback: ADB screencap (returns PNG at native resolution)
            return await self._adb_screenshot(device)

    async def _adb_screenshot(self, device: str) -> Dict[str, Any]:
        """ADB fallback: exec-out screencap -p"""
        proc = await asyncio.create_subprocess_exec(
            "adb", "-s", device, "exec-out", "screencap", "-p",
            stdout=asyncio.subprocess.PIPE)
        stdout, _ = await proc.communicate()
        b64 = base64.b64encode(stdout).decode()
        return {"type": "image", "data": b64, "mimeType": "image/png"}

    async def list_elements_on_screen(self, device: str) -> List[Dict]:
        """List UI elements — handles both MCP and ADB formats."""
        try:
            result = await self._call_mcp_tool(
                "mobile_list_elements_on_screen_mobile-mcp",
                {"device": device}
            )
            # MCP returns nested: {"coordinates": {"x": ..., "width": ...}}
            return self._normalize_elements(result)
        except Exception:
            # Fallback: uiautomator dump
            return await self._adb_list_elements(device)

    async def _adb_list_elements(self, device: str) -> List[Dict]:
        """ADB fallback: uiautomator dump /dev/tty → parse XML."""
        proc = await asyncio.create_subprocess_exec(
            "adb", "-s", device, "shell",
            "uiautomator", "dump", "/dev/tty",
            stdout=asyncio.subprocess.PIPE)
        stdout, _ = await proc.communicate()
        return self._parse_uiautomator_xml(stdout.decode())
```

### 🔦 Flicker Detection Pipeline — 4-Layer Architecture

**Key file**: `backend/app/agents/device_testing/flicker_detection_service.py` (1117 lines)

Detects screen flickers too fast for periodic screenshots (16–200ms). Achieved **19x speedup** over baseline with optimizations.

```
Layer 0: SurfaceFlinger + Logcat     (always-on, zero cost)
  └─ dumpsys gfxinfo → janky frames, avg/max/p90/p99 frame times
  └─ logcat -v time → Choreographer skips, SurfaceFlinger warnings

Layer 1: adb screenrecord            (triggered, 60fps H.264)
  └─ adb shell screenrecord --size 720x1280 --bit-rate 8M --time-limit 30

Layer 2: Frame Extraction + SSIM     (ffmpeg scene filter + parallel)
  └─ ffmpeg -i video.mp4 -vf "select='gt(scene,0.08)'" -vsync vfr frames/%04d.jpg
  └─ ProcessPoolExecutor: parallel SSIM between consecutive frame pairs
  └─ Adaptive threshold: max(0.70, median - 2σ)

Layer 3: GPT-5.4 Vision Verification (semantic classification)
  └─ Sends flagged frame pairs to GPT-5.4
  └─ Answers: "Is this a visual bug or an intentional animation?"
  └─ Populates gpt_analysis field on each FlickerEvent
```

#### Data Structures

```python
@dataclass
class FlickerEvent:
    start_frame: int           # First frame index in the flicker
    end_frame: int             # Last frame index
    start_time: float          # Seconds from recording start
    end_time: float
    duration_ms: float         # Flicker duration in milliseconds
    pattern: str               # "rapid_oscillation" | "sustained_change" | "single_glitch"
    ssim_scores: List[float]   # SSIM values for involved frames
    severity: str              # "HIGH" | "MEDIUM" | "LOW"
    frame_paths: List[str]     # Paths to extracted frame images
    logcat_events: List[LogcatEvent]  # Correlated logcat entries
    gpt_analysis: str          # Layer 3 GPT-5.4 semantic verdict

@dataclass
class FlickerReport:
    session_id: str
    device_id: str
    recording_duration: float
    video_path: str
    total_frames_analyzed: int
    total_scene_frames: int          # After scene filter (60-80% reduction)
    total_flickers_detected: int
    flicker_events: List[FlickerEvent]
    surface_stats: SurfaceStats      # Layer 0 gfxinfo results
    surface_delta: SurfaceDelta      # Pre/post stats comparison
    logcat_events: List[LogcatEvent]
    analysis_time_seconds: float
    ssim_timeline_path: str          # PNG chart of SSIM over time
```

#### SSIM Implementation (numpy-only, no scikit-image)

```python
def _ssim_grayscale(img1: np.ndarray, img2: np.ndarray, win: int = 11) -> float:
    """Block-based SSIM (Wang et al. 2004)."""
    C1, C2 = (0.01 * 255)**2, (0.03 * 255)**2
    img1, img2 = img1.astype(np.float64), img2.astype(np.float64)
    h, w = img1.shape
    bh, bw = h // win, w // win
    b1 = img1[:bh*win, :bw*win].reshape(bh, win, bw, win)
    b2 = img2[:bh*win, :bw*win].reshape(bh, win, bw, win)
    mu1, mu2 = b1.mean(axis=(1,3)), b2.mean(axis=(1,3))
    s1, s2 = b1.var(axis=(1,3)), b2.var(axis=(1,3))
    s12 = ((b1 - mu1[:,None,:,None]) * (b2 - mu2[:,None,:,None])).mean(axis=(1,3))
    num = (2*mu1*mu2 + C1) * (2*s12 + C2)
    den = (mu1**2 + mu2**2 + C1) * (s1 + s2 + C2)
    return float((num / den).mean())

# Parallel SSIM via ProcessPoolExecutor (3-5x speedup)
def _ssim_pair_worker(args: Tuple[str, str, int]) -> float:
    """Picklable top-level worker for ProcessPoolExecutor."""
    path1, path2, resize_w = args
    a1 = _load_gray_array(path1, resize_w)
    a2 = _load_gray_array(path2, resize_w)
    return _ssim_grayscale(a1, a2)

with ProcessPoolExecutor(max_workers=4) as pool:
    pairs = [(frames[i], frames[i+1], 360) for i in range(len(frames)-1)]
    scores = list(pool.map(_ssim_pair_worker, pairs))
```

#### Optimizations (19x Speedup: 18.8s → ~1s)

| Optimization | Speedup | Detail |
|-------------|---------|--------|
| ffmpeg scene pre-filter | 60-80% frame reduction | `select='gt(scene,0.08)'` skips identical frames |
| JPEG extraction | 5-10x smaller | JPEG vs PNG frame files = faster I/O |
| Parallel SSIM | 3-5x | `ProcessPoolExecutor(max_workers=4)` |
| Adaptive threshold | Better accuracy | `max(0.70, median - 2σ)` adapts to dark/light apps |
| Resize to 360px | 4x less pixels | SSIM computed on 360px-wide grayscale |

#### Service Constants

```python
class FlickerDetectionService:
    SSIM_CHANGE_THRESHOLD = 0.92     # Fallback when adaptive disabled
    SSIM_FLICKER_WINDOW_MS = 500     # Oscillations within this = flicker
    MIN_OSCILLATIONS = 2             # Min SSIM dips to count as flicker
    JANK_THRESHOLD_MS = 32.0         # >2 frames at 60fps = janky
    ADAPTIVE_SIGMA = 2.0             # Threshold = median - 2σ
    SCENE_THRESHOLD = 0.08           # ffmpeg scene detection sensitivity
    MAX_SSIM_WORKERS = 4             # Parallel SSIM workers
    ADB_PULL_RETRIES = 3             # Retry pulling video from device
```

---

## 🧠 Expert Knowledge & Patterns

### Subagent Handoff Protocol (Chain of Custody)

```
1. Perceptor (Screen Classifier)  → Structured screen_state + TOON elements
2. Planner   (Device Agent)       → Proposes action based on UI state
3. Guardrail (Action Verifier)    → Boolean verification (Safe/Relevant/Executable)
4. Actor     (Mobile MCP)         → Executes approved action on device
5. Doctor    (Failure Diagnosis)   → Classifies failure + suggests recovery
```

### Boolean Verification (V-Droid Approach)

Every action must pass three binary checks — no numerical confidence scores:
- **is_safe**: Does this action cause data loss or unauthorized access? (YES/NO)
- **is_relevant**: Does this action advance the task goal? (YES/NO)
- **is_executable**: Is the target reachable on the current screen? (YES/NO)
- **Logic**: `approved = is_safe AND is_relevant AND is_executable`

### Model Tiering (2026 Standard)

| Tier | Model | Use Case | Reasoning |
|------|-------|----------|-----------|
| Thinking | GPT-5.4 | Coordinator, Agentic Vision, Flicker Layer 3 | `effort="high"` |
| Core | GPT-5-mini | Screen Classifier, Action Verifier, Failure Diagnosis | `effort="medium"` |
| Utility | GPT-5-nano | MCP tool formatting, data distillation | N/A |

### Failure Taxonomy (OAVR "Reason" Phase)

| Type | Description | Recovery Strategy |
|------|-------------|-------------------|
| PLANNING_ERROR | Wrong action for current state | Re-observe, re-plan |
| PERCEPTION_ERROR | Misinterpreted UI elements | Retry with vision_click |
| ENVIRONMENT_ERROR | App crash, OS dialog, network timeout | Wait, dismiss dialog, restart app |
| EXECUTION_ERROR | Action failed despite element presence | Jitter coordinates, try alternative |

### TOON — Token Optimized Object Notation

Reduces Android accessibility tree payloads by **65%**:
- Strips `com.google.android...` package prefixes
- Filters to only interactive + text-bearing elements
- Removes redundant XML attributes (resource-id, package, etc.)
- Converts nested XML to flat JSON with essential fields only

---

## 🐞 Critical Bug Fixes (All Verified)

| # | Severity | Bug | Root Cause | Fix | Commit |
|---|----------|-----|------------|-----|--------|
| 1 | **CRITICAL** | Bbox misalignment (2.2x offset) | Screenshots at 45% resolution, coords at native | `scale_x = img.width / screen_width` | `c7e5a68` |
| 2 | **CRITICAL** | `async def` in `to_thread()` | Coroutine returned instead of executed | Remove `async` keyword | `a3773ee` |
| 3 | **CRITICAL** | Missing `import asyncio` | `asyncio.to_thread()` at line 552 | Add module-level import | `a3773ee` |
| 4 | **CRITICAL** | Agent duplicate device calls | `parallel_tool_calls=True` race condition | Set to `False` | `a5fb8b4` |
| 5 | **HIGH** | Keyword-only arg mismatch | Positional args to `*` params | Use explicit keyword args | `a3773ee` |
| 6 | **HIGH** | Filepath not reset on failure | Points to non-existent annotated file | Reset to raw path in except | `a3773ee` |
| 7 | **HIGH** | Chef annotation JSON.parse | Unguarded parse + null provider | try-catch + `?? 'Unknown'` | — |
| 8 | **MEDIUM** | Figma Images API rate limit | Plan-tier 100-hour lockout | CV overlay fallback | — |
| 9 | **MEDIUM** | OpenAI 429 rate limits | 5000+ token accessibility trees | TOON format (65% reduction) | — |

---

## 🔄 Core Workflows

### The Ralph Loop (Closed-Loop Verification)
```
1. CODE    → Implement feature or fix
2. LINT    → mypy / eslint verification
3. TEST    → pytest / npm run build
4. ASYNC   → Confirm no async in to_thread()
5. HUD     → Watch emulator stream while agent runs
6. COMMIT  → Only when ALL checks pass
```

### Navigation Test Flow
```
1. adb devices                           # Verify emulator connected
2. cd backend && uvicorn app.main:app    # Start backend
3. cd frontend/test-studio && npm run dev # Start frontend
4. Open http://localhost:5173/demo        # Navigate to demo
5. Type task in chat                      # e.g. "go youtube find video"
6. Watch OAVR loop execute               # SoM annotations + agent narration
7. Verify bbox alignment                  # Should match actual elements
```

---

## 📜 License

MIT © 2026 retention.sh

---

## 🔗 Links

- **Repository**: [github.com/HomenShum/retention](https://github.com/HomenShum/retention)
- **Issues**: [GitHub Issues](https://github.com/HomenShum/retention/issues)
- **MCP Protocol**: [modelcontextprotocol.io](https://modelcontextprotocol.io)