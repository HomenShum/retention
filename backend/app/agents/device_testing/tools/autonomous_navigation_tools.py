"""
Autonomous Navigation Tools - Mobile MCP wrappers for autonomous navigation

These tools wrap Mobile MCP client methods to provide clean interfaces
for the autonomous navigation agent.

Includes smart context compaction to prevent token bloat in long-running tasks.
"""

import asyncio
import logging
import json
import re
from typing import Dict, Any, Optional, List, Tuple

from toon import encode as toon_encode
# from app.agents.coordinator.context_compactor import compact_element_list, store_full_output

logger = logging.getLogger(__name__)

# Global cache to reduce redundant ADB calls within the same turn
# Key: device_id, Value: (timestamp, elements)
_ELEMENT_CACHE: Dict[str, Tuple[float, List[Dict[str, Any]]]] = {}
_CACHE_TTL = 2.0  # 2 second TTL is enough for a single tool turn/auto-screenshot


def _bbox_squash_whitespace(text: str) -> str:
    return " ".join((text or "").split())


def _bbox_dedupe_adjacent_words(text: str) -> str:
    parts = (text or "").split(" ")
    out: List[str] = []
    for w in parts:
        if out and out[-1].lower() == w.lower():
            continue
        out.append(w)
    return " ".join(out)


def _bbox_clean_label(raw: str) -> str:
    """Best-effort cleanup for accessibility labels so pills stay compact/readable."""
    s = _bbox_squash_whitespace(raw)
    if not s:
        return ""
    # Remove repeated punctuation / bullets and common separators noise.
    s = re.sub(r"[\u2022\u2023\u25E6\u2043\u2219]+", " ", s)
    s = _bbox_squash_whitespace(s)
    s = _bbox_dedupe_adjacent_words(s)
    return s


def _bbox_ellipsize(text: str, max_len: int) -> str:
    s = text or ""
    if max_len <= 0:
        return ""
    if len(s) <= max_len:
        return s
    if max_len == 1:
        return "…"
    return s[: max_len - 1] + "…"


# ---------------------------------------------------------------------------
# SoM-style element type classification & color palette
# ---------------------------------------------------------------------------

# Element type → (border_color_RGBA, fill_color_RGBA, tag)
# Colors chosen for maximum contrast + type differentiation (SoM-inspired).
_ELEMENT_PALETTE: Dict[str, Tuple[Tuple[int, ...], Tuple[int, ...], str]] = {
    "button":    ((30, 144, 255, 255),  (30, 144, 255, 180),  "BTN"),     # Dodger blue
    "input":     ((255, 165, 0, 255),   (255, 165, 0, 180),   "INPUT"),   # Orange
    "toggle":    ((148, 103, 189, 255), (148, 103, 189, 180), "TOGGLE"),  # Purple
    "nav":       ((255, 20, 147, 255),  (255, 20, 147, 180),  "NAV"),     # Deep pink
    "image":     ((0, 206, 209, 255),   (0, 206, 209, 180),   "IMG"),     # Dark cyan
    "text":      ((130, 130, 130, 255), (100, 100, 100, 150), "TXT"),     # Gray
    "list":      ((34, 139, 34, 255),   (34, 139, 34, 180),   "LIST"),    # Forest green
    "container": ((169, 169, 169, 200), (120, 120, 120, 120), "BOX"),     # Dark gray
    "unknown":   ((0, 200, 83, 255),    (0, 200, 83, 180),    "ELEM"),    # Green
}

# Class name substrings → element type (checked in order; first match wins)
_CLASS_TYPE_MAP: List[Tuple[str, str]] = [
    # Toggle-like must come before "button" to avoid false match on RadioButton etc.
    ("radiobutton",  "toggle"),
    ("compoundbutton","toggle"),
    ("checkbox",     "toggle"),
    ("switch",       "toggle"),
    ("toggle",       "toggle"),
    ("button",       "button"),
    ("btn",          "button"),
    ("imagebutton",  "button"),
    ("floatingaction","button"),
    ("fab",          "button"),
    ("edittext",     "input"),
    ("textfield",    "input"),
    ("searchview",   "input"),
    ("autocomplete", "input"),
    ("tabview",      "nav"),
    ("tablayout",    "nav"),
    ("tabwidget",    "nav"),
    ("bottomnav",    "nav"),
    ("navigation",   "nav"),
    ("toolbar",      "nav"),
    ("actionbar",    "nav"),
    ("menuitem",     "nav"),
    ("imageview",    "image"),
    ("image",        "image"),
    ("icon",         "image"),
    ("textview",     "text"),
    ("text",         "text"),
    ("recyclerview", "list"),
    ("listview",     "list"),
    ("scrollview",   "list"),
    ("viewpager",    "list"),
    ("framelayout",  "container"),
    ("linearlayout", "container"),
    ("relative",     "container"),
    ("constraint",   "container"),
    ("cardview",     "container"),
    ("viewgroup",    "container"),
]


def _classify_element_type(elem: Dict[str, Any]) -> str:
    """Classify an element into a visual category based on class name and attributes."""
    class_name = (elem.get("class") or elem.get("type") or "").lower()
    # Check class name against known patterns
    for substring, etype in _CLASS_TYPE_MAP:
        if substring in class_name:
            return etype
    # Heuristic: clickable elements without matching class are likely buttons
    if elem.get("clickable") or elem.get("checkable"):
        return "button"
    return "unknown"


def _is_interactive(elem: Dict[str, Any]) -> bool:
    """Check if an element is interactive (clickable, focusable, or editable)."""
    return bool(
        elem.get("clickable")
        or elem.get("focusable")
        or elem.get("checkable")
        or elem.get("editable")
    )


def _element_sort_key(elem: Dict[str, Any]) -> Tuple[int, int]:
    """Sort key: interactive first (0), then by area descending (negative)."""
    interactive = 0 if _is_interactive(elem) else 1
    # Support both nested (MCP: coordinates.width) and flat (ADB: width) layouts
    coords = elem.get("coordinates") or elem.get("bounds") or {}
    w = int(coords.get("width", 0) or 0) or int(elem.get("width", 0) or 0)
    h = int(coords.get("height", 0) or 0) or int(elem.get("height", 0) or 0)
    return (interactive, -(w * h))


def _load_font(size: int):
    """Cross-platform font loading with multiple fallback paths."""
    from PIL import ImageFont
    font_paths = [
        "/System/Library/Fonts/Helvetica.ttc",          # macOS
        "/System/Library/Fonts/SFNSMono.ttf",           # macOS alt
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",  # Linux
        "/usr/share/fonts/TTF/DejaVuSans.ttf",          # Arch Linux
        "C:/Windows/Fonts/arial.ttf",                    # Windows
        "C:/Windows/Fonts/segoeui.ttf",                  # Windows alt
    ]
    for path in font_paths:
        try:
            return ImageFont.truetype(path, size)
        except (OSError, IOError):
            continue
    return ImageFont.load_default()


def _bbox_measure_multiline(draw, text: str, font, spacing: int = 2) -> Tuple[int, int]:
    """Measure multiline text size in a Pillow-version-tolerant way.

    Prefer newer `textbbox`, but gracefully fall back if needed.
    """
    lines = (text or "").split("\n")
    if not lines:
        return 0, 0

    widths: List[int] = []
    heights: List[int] = []
    for line in lines:
        try:
            bbox = draw.textbbox((0, 0), line, font=font)
            w = int(bbox[2] - bbox[0])
            h = int(bbox[3] - bbox[1])
        except Exception:
            try:
                w, h = draw.textsize(line, font=font)  # legacy Pillow
            except Exception:
                w, h = (0, 0)
        widths.append(int(w))
        heights.append(int(h))

    total_h = sum(heights) + spacing * max(0, len(lines) - 1)
    return (max(widths) if widths else 0), total_h


def _bbox_rects_overlap(a: Tuple[int, int, int, int], b: Tuple[int, int, int, int], pad: int = 2) -> bool:
    return not (
        a[2] + pad <= b[0]
        or b[2] + pad <= a[0]
        or a[3] + pad <= b[1]
        or b[3] + pad <= a[1]
    )


def _bbox_find_label_position(
    *,
    box: Tuple[int, int, int, int],
    label_size: Tuple[int, int],
    image_size: Tuple[int, int],
    placed: List[Tuple[int, int, int, int]],
    margin: int = 2,
    max_shifts: int = 10,
) -> Tuple[int, int, Tuple[int, int, int, int]]:
    """Greedy placement: try above/around the box, then shift to avoid label-label overlap."""
    x1, y1, x2, y2 = box
    lw, lh = label_size
    iw, ih = image_size

    def clamp(v: int, lo: int, hi: int) -> int:
        return max(lo, min(v, hi))

    # Candidate anchors (unclamped)
    candidates = [
        (x1, y1 - lh - margin),  # above-left
        (x1, y2 + margin),       # below-left
        (x2 - lw, y1 - lh - margin),  # above-right
        (x2 - lw, y2 + margin),       # below-right
        (x1, y1),                # inside top-left (fallback)
    ]

    for (cx, cy) in candidates:
        x = clamp(int(cx), 0, max(0, iw - lw))
        y0 = clamp(int(cy), 0, max(0, ih - lh))

        for shift in range(max_shifts + 1):
            # If starting above the box, shift upward first; otherwise shift downward.
            direction = -1 if cy < y1 else 1
            y = y0 + direction * shift * (lh + margin)
            y = clamp(int(y), 0, max(0, ih - lh))

            rect = (x, y, x + lw, y + lh)
            if not any(_bbox_rects_overlap(rect, r) for r in placed):
                return x, y, rect

    # Worst-case: clamped inside top-left
    x = max(min(x1, iw - lw), 0)
    y = max(min(y1, ih - lh), 0)
    rect = (x, y, x + lw, y + lh)
    return x, y, rect


def create_autonomous_navigation_tools(mobile_mcp_client, device_id: str) -> Dict[str, Any]:
    """
    Create autonomous navigation tools that wrap Mobile MCP client.
    
    Args:
        mobile_mcp_client: MobileMCPClient instance
        device_id: Target device ID
        
    Returns:
        Dictionary of tool functions
    """
    
    async def list_elements_on_screen(device_id: str = device_id) -> str:
        """
        List all interactive elements on the current screen.

        Returns accessibility tree with element types, text, labels, and coordinates in TOON format
        (Token-Oriented Object Notation) for efficient token usage.

        Use this to understand what's on the screen before taking any action.

        IMPORTANT: If this returns an empty array, the app may still be loading or the screen
        has no interactive elements. Try waiting a moment and calling again, or take a screenshot
        to see what's actually on the screen.

        Args:
            device_id: Device identifier (default: current device)

        Returns:
            TOON-formatted string with elements array containing:
            - type: Element type (Button, TextField, etc.)
            - text: Text content
            - label: Accessibility label
            - coordinates: {x, y, width, height}

            Example TOON format:
            elements[3] count:
              android.widget.Button "Click me" Submit {x:100,y:200}
              android.widget.TextView Hello Greeting {x:50,y:100}
              3
        """
        try:
            import time
            # Check cache first
            now = time.time()
            cached_time, cached_elements = _ELEMENT_CACHE.get(device_id, (0, []))
            if cached_elements and (now - cached_time) < _CACHE_TTL:
                logger.info(f"Using cached elements ({len(cached_elements)}) for list_elements_on_screen")
                result = cached_elements
            else:
                result = await mobile_mcp_client.list_elements_on_screen(device_id)

                # Cache the result
                if isinstance(result, list) and result:
                    _ELEMENT_CACHE[device_id] = (now, result)
                elif isinstance(result, dict) and result.get("elements"):
                    _ELEMENT_CACHE[device_id] = (now, result["elements"])
            # result is a Python list - use smart compaction for token efficiency
            if isinstance(result, list):
                from app.agents.coordinator.context_compactor import compact_element_list, store_full_output
                if len(result) == 0:
                    logger.warning(f"list_elements_on_screen returned empty array for {device_id}. App may be loading or screen has no elements.")
                    return json.dumps({
                        "elements": [],
                        "count": 0,
                        "message": "No elements found. The app may still be loading, or the screen has no interactive elements. Try taking a screenshot to see what's on screen, or wait a moment and try again."
                    })

                # Use smart compaction: semantic summary + external storage for full data
                # This preserves navigation capability while reducing context tokens
                try:
                    # Store full element list externally for retrieval if needed
                    ref_id = store_full_output(result, "list_elements_on_screen")

                    # Generate compact semantic summary
                    compact_output = compact_element_list(result, max_actionable=20)

                    # Log savings
                    full_json_size = len(json.dumps({"elements": result, "count": len(result)}))
                    compact_size = len(compact_output)
                    savings_pct = round((1 - compact_size / full_json_size) * 100)
                    logger.info(f"📦 Compacted {len(result)} elements: {full_json_size} -> {compact_size} chars ({savings_pct}% reduction, ref_id={ref_id})")

                    return f"{compact_output}\n\n[Full element data stored: ref_id={ref_id}]"
                except Exception as compact_error:
                    logger.warning(f"Failed to compact elements, falling back to TOON: {compact_error}")
                    # Fallback to TOON format
                    try:
                        data = {"elements": result, "count": len(result)}
                        toon_output = toon_encode(data)
                        return toon_output
                    except Exception:
                        return json.dumps({"elements": result, "count": len(result)})
            else:
                logger.error(f"Unexpected result type from list_elements_on_screen: {type(result)}")
                return json.dumps({"error": f"Unexpected result type: {type(result)}", "elements": []})
        except Exception as e:
            logger.error(f"Error listing elements: {e}")
            return json.dumps({"error": str(e), "elements": []})
    
    async def take_screenshot(
        device_id: str = device_id,
        draw_bounding_boxes: bool = True,
        analysis_topics: Optional[List[str]] = None,
        use_agentic_vision: bool = False,
        zoom_targets: Optional[List[str]] = None
    ) -> str:
        """
        Take a screenshot of the current screen and get a visual analysis.

        By default, uses OpenAI Vision (gpt-5-mini). When use_agentic_vision=True,
        uses Gemini 3 Flash Agentic Vision with Think-Act-Observe loop for:
        - Automatic zooming on fine-grained details
        - Image annotation to ground reasoning
        - Code execution for precise analysis

        Optionally draws bounding boxes around interactive elements with coordinate labels.

        Use this when you need to understand what's visually on the screen beyond
        the accessibility tree (e.g., images, layout, visual elements, colors, branding).

        Args:
            device_id: Device identifier (default: current device)
            draw_bounding_boxes: If True, draws bounding boxes around elements with coordinates (default: True)
            analysis_topics: Optional list of topics to focus analysis on (e.g., ["buttons", "dialogs", "input_fields"])
                           If provided, analysis will be tailored to these topics for cost efficiency.
                           If None, performs full generic analysis.
            use_agentic_vision: If True, uses Gemini 3 Flash Agentic Vision instead of OpenAI Vision.
                               Provides 5-10% quality boost for fine-grained vision tasks. (default: False)
            zoom_targets: Optional list of areas to focus zooming on when using Agentic Vision
                         (e.g., ["top-right corner", "bottom navigation", "small text fields"])

        Returns:
            A text description of what's visible on the screen.
            Does NOT return the base64 image data (to avoid polluting context).
        """
        def _draw_bounding_boxes_threaded(img_path: str, elements: List[Dict[str, Any]], out_path: str, screen_size: Optional[Tuple[int, int]] = None):
            """PIL operations moved to thread to avoid blocking event loop.

            Args:
                screen_size: (width, height) of the device screen in native pixels.
                    Element coordinates are in this space. If the screenshot image
                    is smaller (e.g. JPEG-compressed by Mobile MCP), coordinates
                    are scaled down to match the image.
            """
            from PIL import Image, ImageDraw
            img = Image.open(img_path).convert("RGBA")
            base_draw = ImageDraw.Draw(img)
            overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
            overlay_draw = ImageDraw.Draw(overlay)

            # Compute coordinate scale factors: element coords → image pixels
            if screen_size and screen_size[0] > 0 and screen_size[1] > 0:
                scale_x = img.width / screen_size[0]
                scale_y = img.height / screen_size[1]
            else:
                scale_x = 1.0
                scale_y = 1.0

            # Scale fonts to image resolution
            label_font_size = max(16, img.width // 54)
            small_font_size = max(12, img.width // 72)
            font = _load_font(label_font_size)
            small_font = _load_font(small_font_size)
            box_width = max(2, img.width // 360)

            sorted_elements = sorted(elements, key=_element_sort_key)
            max_elements = 40
            min_area_interactive = 100
            min_area_static = 400
            drawn = 0
            placed_labels: List[Tuple[int, int, int, int]] = []
            type_counts: Dict[str, int] = {}

            for elem in sorted_elements:
                coords = elem.get("coordinates") or elem.get("bounds") or {}
                raw_x = int(coords.get("x", 0) or 0)
                raw_y = int(coords.get("y", 0) or 0)
                raw_w = int(coords.get("width", 0) or 0)
                raw_h = int(coords.get("height", 0) or 0)

                if raw_w <= 0 or raw_h <= 0: continue

                # Scale from device-native coordinates to image coordinates
                x = int(raw_x * scale_x)
                y = int(raw_y * scale_y)
                width = int(raw_w * scale_x)
                height = int(raw_h * scale_y)

                interactive = _is_interactive(elem)
                area = width * height
                if interactive and area < min_area_interactive: continue
                if not interactive and area < min_area_static: continue

                etype = _classify_element_type(elem)
                if etype == "container": continue

                raw_label = (elem.get("label") or elem.get("text") or elem.get("name") or elem.get("identifier") or "").strip()
                if not raw_label: raw_label = (elem.get("content_desc") or "").strip()
                if not raw_label:
                    rid = elem.get("resource_id") or ""
                    if rid: raw_label = rid.split("/")[-1].replace("_", " ")
                if not raw_label: continue

                raw_label = _bbox_clean_label(raw_label)
                if not raw_label: continue

                border_color, fill_color, tag = _ELEMENT_PALETTE.get(etype, _ELEMENT_PALETTE["unknown"])
                display_idx = drawn + 1
                short = _bbox_ellipsize(raw_label, 30)
                label = f"#{display_idx} [{tag}]\n{short}"

                x1, y1 = x, y
                x2, y2 = x + width, y + height
                base_draw.rectangle([x1, y1, x2, y2], outline=border_color, width=box_width)

                text_w, text_h = _bbox_measure_multiline(overlay_draw, label, small_font, spacing=2)
                pad_x, pad_y = 6, 4
                bg_w, bg_h = text_w + pad_x * 2, text_h + pad_y * 2

                bg_x1, bg_y1, placed_rect = _bbox_find_label_position(box=(x1, y1, x2, y2), label_size=(bg_w, bg_h), image_size=(img.width, img.height), placed=placed_labels, margin=2)
                placed_labels.append(placed_rect)
                bg_x2, bg_y2 = bg_x1 + bg_w, bg_y1 + bg_h

                bg_fill = (0, 0, 0, 190)
                bg_outline = border_color[:3] + (220,)
                if hasattr(overlay_draw, "rounded_rectangle"):
                    overlay_draw.rounded_rectangle([bg_x1, bg_y1, bg_x2, bg_y2], radius=6, fill=bg_fill, outline=bg_outline, width=1)
                else:
                    overlay_draw.rectangle([bg_x1, bg_y1, bg_x2, bg_y2], fill=bg_fill, outline=bg_outline, width=1)

                overlay_draw.multiline_text((bg_x1 + pad_x, bg_y1 + pad_y), label, fill=(255, 255, 255, 255), font=small_font, spacing=2)
                drawn += 1
                type_counts[tag] = type_counts.get(tag, 0) + 1
                if drawn >= max_elements: break

            if drawn > 0:
                img = Image.alpha_composite(img, overlay)
                legend_overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
                legend_draw = ImageDraw.Draw(legend_overlay)
                legend_font = _load_font(max(12, img.width // 80))
                legend_items = [f"{tag}:{cnt}" for tag, cnt in sorted(type_counts.items())]
                legend_text = "  ".join(legend_items) + f"  TOTAL:{drawn}"
                tw, th = _bbox_measure_multiline(legend_draw, legend_text, legend_font)
                lx, ly = 8, img.height - th - 12
                legend_draw.rectangle([0, ly - 6, tw + 20, img.height], fill=(0, 0, 0, 200))
                legend_draw.text((lx, ly), legend_text, fill=(255, 255, 255, 240), font=legend_font)
                img = Image.alpha_composite(img, legend_overlay)

            img.save(out_path)
            return drawn, type_counts

        try:
            import base64
            import os
            import time
            from datetime import datetime

            result = await mobile_mcp_client.take_screenshot(device_id)

            # Handle error responses from Mobile MCP client
            if isinstance(result, dict) and "error" in result:
                error_msg = result.get("error", "Unknown error")
                logger.error(f"Screenshot failed: {error_msg}")
                return f"Failed to capture screenshot: {error_msg}"

            if isinstance(result, dict) and result.get("type") == "image":
                # Save screenshot to file for reference (aligned with FastAPI static mount in main.py)
                from pathlib import Path
                backend_dir = Path(__file__).resolve().parents[4]
                screenshot_dir = backend_dir / "screenshots"
                screenshot_dir.mkdir(parents=True, exist_ok=True)
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                filename = f"{device_id}_{timestamp}.png"
                filepath = str(screenshot_dir / filename)

                base64_data = result.get("data", "")
                if not base64_data:
                    return "Failed to capture screenshot - no image data in response"

                # Save original screenshot to file
                with open(filepath, "wb") as f:
                    f.write(base64.b64decode(base64_data))

                # Draw SoM-style color-coded bounding boxes if requested
                annotated_filepath = filepath
                if draw_bounding_boxes:
                    try:
                        # Check cache first for elements to avoid redundant ADB dump
                        now = time.time()
                        cached_time, cached_elements = _ELEMENT_CACHE.get(device_id, (0, []))
                        
                        if cached_elements and (now - cached_time) < _CACHE_TTL:
                            logger.info(f"Using cached elements ({len(cached_elements)}) for bounding boxes")
                            elements = cached_elements
                        else:
                            # Get elements on screen
                            elements_result = await mobile_mcp_client.list_elements_on_screen(device_id)
                            elements = []
                            if isinstance(elements_result, dict):
                                elements = elements_result.get("elements", [])
                            elif isinstance(elements_result, list):
                                elements = elements_result
                            
                            # Cache the result
                            if elements:
                                _ELEMENT_CACHE[device_id] = (now, elements)

                        if elements:
                            # Get device screen size for coordinate scaling
                            # Element coords are in native resolution (e.g. 1080x2400)
                            # but screenshot may be scaled down (e.g. 486x1080 JPEG)
                            device_screen_size = None
                            try:
                                size_text = await mobile_mcp_client.get_screen_size(device_id)
                                # Parse "Screen size is 1080x2400 pixels"
                                import re as _re
                                size_match = _re.search(r'(\d+)\s*x\s*(\d+)', size_text)
                                if size_match:
                                    device_screen_size = (int(size_match.group(1)), int(size_match.group(2)))
                                    logger.info(f"Device screen size: {device_screen_size}")
                            except Exception as size_err:
                                logger.warning(f"Could not get screen size, bbox scaling disabled: {size_err}")

                            # Run PIL operations in a separate thread to avoid blocking the event loop
                            annotated_filename = f"{device_id}_{timestamp}_annotated.png"
                            annotated_filepath = str(screenshot_dir / annotated_filename)

                            drawn, type_counts = await asyncio.to_thread(
                                _draw_bounding_boxes_threaded,
                                filepath,
                                elements,
                                annotated_filepath,
                                device_screen_size
                            )
                            
                            if drawn > 0:
                                logger.info(
                                    f"Saved SoM-annotated screenshot: {drawn} elements "
                                    f"({type_counts}) to {annotated_filepath}"
                                )
                            else:
                                annotated_filepath = filepath
                        else:
                            logger.info("No elements found to draw bounding boxes")
                    except Exception as bbox_error:
                        logger.warning(f"Failed to draw bounding boxes: {bbox_error}")
                        annotated_filepath = filepath  # Fall back to original screenshot

                # Branch: Use Agentic Vision (Gemini 3 Flash) if requested
                if use_agentic_vision:
                    try:
                        from .agentic_vision_service import analyze_screenshot_with_agentic_vision
                        
                        # Read the screenshot for agentic vision
                        with open(annotated_filepath, "rb") as img_file:
                            image_bytes = img_file.read()
                        
                        # Build query from analysis_topics if provided
                        if analysis_topics:
                            query = f"Analyze this mobile app screenshot focusing on: {', '.join(analysis_topics)}"
                        else:
                            query = "Analyze this mobile app screenshot. Identify the app, screen state, key UI elements, and any notable visual features."
                        
                        # Determine mode based on zoom_targets
                        mode = "zoom" if zoom_targets else "auto"
                        
                        logger.info(f"🔍 Using Agentic Vision (Gemini 3 Flash) for screenshot analysis")
                        vision_result = await analyze_screenshot_with_agentic_vision(
                            image_bytes,
                            query,
                            mode=mode,
                            zoom_targets=zoom_targets
                        )
                        
                        if vision_result.success:
                            step_info = ""
                            if vision_result.steps:
                                step_info = f"\n\n🔄 Agentic Steps: {vision_result.total_steps}"
                            
                            bbox_info = ""
                            if draw_bounding_boxes and annotated_filepath != filepath:
                                bbox_info = "\n\n🎯 Bounding boxes drawn on screenshot showing element locations."
                            
                            return f"""📸 Screenshot Analysis (Gemini Agentic Vision):

{vision_result.final_analysis}
{step_info}
{bbox_info}

Screenshot saved to: {annotated_filepath}"""
                        else:
                            logger.warning(f"Agentic Vision failed, falling back to OpenAI: {vision_result.error}")
                            # Fall through to OpenAI Vision below
                    
                    except ImportError as e:
                        logger.warning(f"Agentic Vision not available, falling back to OpenAI: {e}")
                        # Fall through to OpenAI Vision below
                    except Exception as av_error:
                        logger.warning(f"Agentic Vision error, falling back to OpenAI: {av_error}")
                        # Fall through to OpenAI Vision below

                # Analyze screenshot with OpenAI Vision API (with fallback models)
                # Use annotated screenshot if available, otherwise use original
                try:
                    import openai
                    from ....observability.tracing import get_traced_client
                    api_key = os.getenv("OPENAI_API_KEY")
                    if not api_key:
                        logger.warning("OPENAI_API_KEY not set - skipping vision analysis")
                        return f"Screenshot saved to {annotated_filepath}. Vision analysis unavailable (no API key)."

                    client = get_traced_client(openai.OpenAI(api_key=api_key))

                    # Read the annotated screenshot (or original if annotation failed)
                    with open(annotated_filepath, "rb") as img_file:
                        annotated_base64 = base64.b64encode(img_file.read()).decode('utf-8')

                    # Model fallback chain for vision tasks (January 2026)
                    # GPT-5.4 is flagship with best vision, then GPT-5, then mini/nano
                    models_to_try = [
                        "gpt-5.4",         # Primary: flagship best vision + reasoning
                        "gpt-5",           # Fallback 1: strong vision capabilities
                        "gpt-5-mini",      # Fallback 2: good but intermittent
                        "gpt-5-nano",      # Fallback 3: last resort
                    ]

                    # Build analysis prompt based on topics if provided
                    if analysis_topics:
                        # Topic-focused analysis for cost efficiency
                        topics_text = ", ".join(analysis_topics)
                        vision_prompt = f"""Analyze this mobile app screenshot and focus specifically on these aspects:
Topics to analyze: {topics_text}

For each topic, provide:
- What elements are relevant to this topic?
- Where are they located? (use bounding box #index labels if visible)
- What are their current states?

Be concise and focus only on the specified topics. Reference bounding boxes by their #index numbers."""
                    else:
                        # Default full analysis
                        vision_prompt = """Analyze this mobile app screenshot and provide a concise description of:
1. What app/screen is this? (identify the app and current screen)
2. Main visual elements (buttons, images, text, icons)
3. Layout and structure (header, content area, navigation)
4. Key interactive elements visible (note: green bounding boxes show element locations with #index labels)
5. Any notable visual features (colors, branding, images)

Be specific and concise. Focus on what's actionable for navigation. If bounding boxes are visible, reference them by their #index numbers."""

                    vision_response = None
                    last_error = None

                    for model in models_to_try:
                        try:
                            logger.info(f"Attempting vision analysis with model: {model}, topics: {analysis_topics or 'full'}")
                            vision_response = client.chat.completions.create(
                                model=model,
                                messages=[
                                    {
                                        "role": "user",
                                        "content": [
                                            {
                                                "type": "text",
                                                "text": vision_prompt
                                            },
                                            {
                                                "type": "image_url",
                                                "image_url": {
                                                    "url": f"data:image/png;base64,{annotated_base64}",
                                                    "detail": "high"
                                                }
                                            }
                                        ]
                                    }
                                ],
                                max_completion_tokens=500
                            )
                            logger.info(f"✅ Vision analysis succeeded with model: {model}")
                            break  # Success, exit loop
                        except Exception as model_error:
                            last_error = model_error
                            error_msg = str(model_error)
                            logger.warning(f"Model {model} failed: {error_msg}")
                            # Continue to next model
                            continue

                    if vision_response is None:
                        # All models failed
                        logger.error(f"All vision models failed. Last error: {last_error}")
                        return f"Screenshot saved to {annotated_filepath}. Vision analysis failed: {str(last_error)}"

                    analysis = vision_response.choices[0].message.content

                    # Build response with bounding box info
                    bbox_info = ""
                    if draw_bounding_boxes and annotated_filepath != filepath:
                        bbox_info = "\n\n🎯 Bounding boxes drawn on screenshot showing element locations and coordinates."

                    return f"""📸 Screenshot Analysis (Vision):

{analysis}
{bbox_info}

Screenshot saved to: {annotated_filepath}"""

                except Exception as vision_error:
                    logger.error(f"Vision API error: {vision_error}")
                    return f"Screenshot saved to {annotated_filepath}. Vision analysis failed: {str(vision_error)}"

            else:
                # Log detailed info for debugging (per section 8 data inspection protocol)
                result_type = type(result).__name__
                result_keys = list(result.keys()) if isinstance(result, dict) else "N/A"
                logger.error(f"Unexpected screenshot result format. Type: {result_type}, Keys: {result_keys}")
                return f"Failed to capture screenshot - unexpected format (type: {result_type})"
        except Exception as e:
            logger.error(f"Error taking screenshot: {e}")
            return f"Error capturing screenshot: {str(e)}"
    
    async def click_at_coordinates(device_id: str, x: int, y: int) -> str:
        """
        Click/tap at specific screen coordinates.
        
        Use coordinates from list_elements_on_screen to tap on specific elements.
        
        Args:
            device_id: Device identifier
            x: X coordinate in pixels
            y: Y coordinate in pixels
            
        Returns:
            Confirmation message
        """
        try:
            result = await mobile_mcp_client.click_on_screen(device_id, x, y)
            return result
        except Exception as e:
            logger.error(f"Error clicking at ({x}, {y}): {e}")
            return json.dumps({"error": str(e)})
    
    async def type_text(device_id: str, text: str, submit: bool = False) -> str:
        """
        Type text into the currently focused input field.
        
        Make sure to tap on a text field first to focus it before typing.
        
        Args:
            device_id: Device identifier
            text: Text to type
            submit: Whether to press Enter/Return after typing
            
        Returns:
            Confirmation message
        """
        try:
            result = await mobile_mcp_client.type_keys(device_id, text, submit)
            return result
        except Exception as e:
            logger.error(f"Error typing text: {e}")
            return json.dumps({"error": str(e)})
    
    async def swipe_on_screen(
        device_id: str,
        direction: str,
        x: Optional[int] = None,
        y: Optional[int] = None,
        distance: Optional[int] = None
    ) -> str:
        """
        Perform a swipe gesture on the screen.
        
        Use this to scroll content or navigate between screens.
        
        Args:
            device_id: Device identifier
            direction: Swipe direction ("up", "down", "left", "right")
            x: Optional starting X coordinate (default: center)
            y: Optional starting Y coordinate (default: center)
            distance: Optional swipe distance in pixels
            
        Returns:
            Confirmation message
        """
        try:
            result = await mobile_mcp_client.swipe_on_screen(
                device_id, direction, x, y, distance
            )
            return result
        except Exception as e:
            logger.error(f"Error swiping {direction}: {e}")
            return json.dumps({"error": str(e)})
    
    async def press_button(device_id: str, button: str) -> str:
        """
        Press a physical or virtual device button.
        
        Use this to navigate (HOME, BACK) or control volume.
        
        Args:
            device_id: Device identifier
            button: Button name ("HOME", "BACK", "VOLUME_UP", "VOLUME_DOWN", etc.)
            
        Returns:
            Confirmation message
        """
        try:
            result = await mobile_mcp_client.press_button(device_id, button)
            return result
        except Exception as e:
            logger.error(f"Error pressing button {button}: {e}")
            return json.dumps({"error": str(e)})
    
    async def launch_app(device_id: str, package_name: str) -> str:
        """
        Launch an application by its package name.
        
        Use list_apps first to find the correct package name.
        
        Args:
            device_id: Device identifier
            package_name: App package name (e.g., "com.google.android.youtube")
            
        Returns:
            Confirmation message
        """
        try:
            result = await mobile_mcp_client.launch_app(device_id, package_name)
            return result
        except Exception as e:
            logger.error(f"Error launching app {package_name}: {e}")
            return json.dumps({"error": str(e)})
    
    async def list_apps(device_id: str = device_id) -> str:
        """
        List all installed applications on the device.
        
        Returns app display names and package names.
        
        Args:
            device_id: Device identifier (default: current device)
            
        Returns:
            String listing apps with their package names
        """
        try:
            result = await mobile_mcp_client.list_apps(device_id)
            return result
        except Exception as e:
            logger.error(f"Error listing apps: {e}")
            return json.dumps({"error": str(e)})
    
    async def get_screen_size(device_id: str = device_id) -> str:
        """
        Get the screen dimensions in pixels.
        
        Useful for calculating relative coordinates.
        
        Args:
            device_id: Device identifier (default: current device)
            
        Returns:
            Screen size information (width x height)
        """
        try:
            result = await mobile_mcp_client.get_screen_size(device_id)
            return result
        except Exception as e:
            logger.error(f"Error getting screen size: {e}")
            return json.dumps({"error": str(e)})
    
    # ========== PARALLEL MULTI-DEVICE TOOLS ==========
    # These tools enable true concurrent execution across multiple devices

    async def take_screenshots_parallel(device_ids: List[str]) -> str:
        """
        Take screenshots on multiple devices SIMULTANEOUSLY.

        Use this when you need to observe multiple devices at once - much faster
        than calling take_screenshot sequentially for each device.

        Args:
            device_ids: List of device IDs to screenshot (e.g., ["emulator-5556", "emulator-5560"])

        Returns:
            JSON object with results per device, including vision analysis for each
        """
        import asyncio

        async def capture_one(dev_id: str) -> Dict[str, Any]:
            try:
                # Call the single-device screenshot function
                result = await take_screenshot(device_id=dev_id, draw_bounding_boxes=True)
                return {"device_id": dev_id, "status": "success", "analysis": result}
            except Exception as e:
                logger.error(f"Parallel screenshot failed for {dev_id}: {e}")
                return {"device_id": dev_id, "status": "error", "error": str(e)}

        # Run all screenshots in parallel
        tasks = [capture_one(dev_id) for dev_id in device_ids]
        results = await asyncio.gather(*tasks, return_exceptions=False)

        logger.info(f"📸 Parallel screenshots completed for {len(device_ids)} devices")
        return json.dumps({"results": results, "device_count": len(device_ids)}, indent=2)

    async def list_elements_parallel(device_ids: List[str]) -> str:
        """
        List elements on multiple devices SIMULTANEOUSLY.

        Use this when you need to understand the state of multiple devices at once -
        much faster than calling list_elements_on_screen sequentially.

        Args:
            device_ids: List of device IDs to query (e.g., ["emulator-5556", "emulator-5560"])

        Returns:
            JSON object with element lists per device (compacted for token efficiency)
        """
        import asyncio

        async def list_one(dev_id: str) -> Dict[str, Any]:
            try:
                # Call the single-device list function
                result = await list_elements_on_screen(device_id=dev_id)
                return {"device_id": dev_id, "status": "success", "elements": result}
            except Exception as e:
                logger.error(f"Parallel element listing failed for {dev_id}: {e}")
                return {"device_id": dev_id, "status": "error", "error": str(e)}

        # Run all listings in parallel
        tasks = [list_one(dev_id) for dev_id in device_ids]
        results = await asyncio.gather(*tasks, return_exceptions=False)

        logger.info(f"📋 Parallel element listing completed for {len(device_ids)} devices")
        return json.dumps({"results": results, "device_count": len(device_ids)}, indent=2)

    async def execute_parallel_actions(actions_json: str) -> str:
        """
        Execute multiple device actions SIMULTANEOUSLY across different devices.

        This is the most powerful parallel tool - use it when you need to perform
        different actions on different devices at the same time.

        Args:
            actions_json: JSON string containing a list of action objects. Each action has:
                - device_id: Target device (e.g., "emulator-5556")
                - action: One of "click", "type", "swipe", "press_button", "launch_app", "screenshot", "list_elements"
                - params: Action-specific parameters object:
                    - click: {"x": 540, "y": 960}
                    - type: {"text": "hello", "submit": true}
                    - swipe: {"direction": "up"}
                    - press_button: {"button": "HOME"}
                    - launch_app: {"package_name": "com.google.android.youtube"}

        Example actions_json:
            '[{"device_id": "emulator-5556", "action": "launch_app", "params": {"package_name": "com.google.android.youtube"}}, {"device_id": "emulator-5560", "action": "launch_app", "params": {"package_name": "com.android.chrome"}}]'

        Returns:
            JSON object with results per action
        """
        import asyncio

        # Parse the JSON string to get the list of actions
        try:
            actions = json.loads(actions_json)
            if not isinstance(actions, list):
                return json.dumps({"error": "actions_json must be a JSON array of action objects"})
        except json.JSONDecodeError as e:
            return json.dumps({"error": f"Invalid JSON: {str(e)}"})

        async def execute_one(action_def: Dict[str, Any]) -> Dict[str, Any]:
            dev_id = action_def.get("device_id", device_id)
            action_type = action_def.get("action", "unknown")
            params = action_def.get("params", {})

            try:
                if action_type == "click":
                    result = await click_at_coordinates(
                        x=params.get("x", 0),
                        y=params.get("y", 0),
                        device_id=dev_id
                    )
                elif action_type == "type":
                    result = await type_text(
                        text=params.get("text", ""),
                        submit=params.get("submit", False),
                        device_id=dev_id
                    )
                elif action_type == "swipe":
                    result = await swipe_on_screen(
                        direction=params.get("direction", "up"),
                        x=params.get("x"),
                        y=params.get("y"),
                        device_id=dev_id
                    )
                elif action_type == "press_button":
                    result = await press_button(
                        button=params.get("button", "HOME"),
                        device_id=dev_id
                    )
                elif action_type == "launch_app":
                    result = await launch_app(
                        package_name=params.get("package_name", ""),
                        device_id=dev_id
                    )
                elif action_type == "screenshot":
                    result = await take_screenshot(device_id=dev_id)
                elif action_type == "list_elements":
                    result = await list_elements_on_screen(device_id=dev_id)
                else:
                    result = f"Unknown action type: {action_type}"

                return {
                    "device_id": dev_id,
                    "action": action_type,
                    "status": "success",
                    "result": result
                }
            except Exception as e:
                logger.error(f"Parallel action '{action_type}' failed for {dev_id}: {e}")
                return {
                    "device_id": dev_id,
                    "action": action_type,
                    "status": "error",
                    "error": str(e)
                }

        # Run all actions in parallel
        tasks = [execute_one(action) for action in actions]
        results = await asyncio.gather(*tasks, return_exceptions=False)

        # Summary
        success_count = sum(1 for r in results if r.get("status") == "success")
        error_count = len(results) - success_count

        logger.info(f"⚡ Parallel actions completed: {success_count} success, {error_count} errors across {len(actions)} actions")

        return json.dumps({
            "results": results,
            "summary": {
                "total": len(actions),
                "success": success_count,
                "errors": error_count
            }
        }, indent=2)

    async def vision_click(
        device_id: str = device_id,
        query: str = "Find the click center coordinates for the target element",
        target_description: str = "the main action button"
    ) -> str:
        """
        Use Agentic Vision to find something visually and click it.
        
        Use this when list_elements_on_screen fails to find a specific element 
        that is visually present on the screen.
        
        Args:
            device_id: Device identifier
            query: Specific query for the vision agent (e.g., "Find the search icon at top right")
            target_description: Human-readable description for logging
            
        Returns:
            Result of the click action
        """
        try:
            from ..agentic_vision_service import AgenticVisionClient
            import base64
            import re
            
            # 1. Take screenshot
            result = await mobile_mcp_client.take_screenshot(device_id)
            if not (isinstance(result, dict) and result.get("type") == "image"):
                return "Failed to take screenshot for vision_click"
            
            image_bytes = base64.b64decode(result.get("data", ""))
            
            # 2. Get screen size
            size_result = await mobile_mcp_client.get_screen_size(device_id)
            width, height = 1080, 2400 # defaults
            try:
                if "x" in str(size_result):
                    parts = str(size_result).split("x")
                    width = int(re.search(r'\d+', parts[0]).group())
                    height = int(re.search(r'\d+', parts[1]).group())
            except (ValueError, AttributeError, IndexError):
                pass

            # 3. Call Agentic Vision to find coordinates
            client = AgenticVisionClient()
            vision_query = f"""
            Identify the location of: {query}
            
            Based on the screen size {width}x{height}, find the exact center (x, y) coordinates of this element.
            Provide your final answer as: COORDINATES: (x, y)
            """
            
            vision_result = await client.multi_step_vision(image_bytes, vision_query)
            
            if not vision_result.success:
                return f"Vision finding failed: {vision_result.error}"
            
            # 4. Parse coordinates from final_analysis
            match = re.search(r"COORDINATES:\s*\(?(\d+),\s*(\d+)\)?", vision_result.final_analysis)
            if not match:
                match = re.search(r"(\d+),\s*(\d+)", vision_result.final_analysis)
            
            if match:
                x, y = int(match.group(1)), int(match.group(2))
                # 5. Execute click
                logger.info(f"🎯 Vision found {target_description} at ({x}, {y}). Clicking...")
                click_result = await mobile_mcp_client.click_on_screen(device_id, x, y)
                return f"✅ Vision clicked {target_description} at ({x}, {y}). Result: {click_result}"
            else:
                return f"Vision found the element but failed to provide coordinates consistently. Analysis: {vision_result.final_analysis}"
                
        except Exception as e:
            logger.error(f"Error in vision_click: {e}")
            return f"Error: {str(e)}"

    return {
        # Single-device tools
        "list_elements_on_screen": list_elements_on_screen,
        "take_screenshot": take_screenshot,
        "click_at_coordinates": click_at_coordinates,
        "type_text": type_text,
        "swipe_on_screen": swipe_on_screen,
        "press_button": press_button,
        "launch_app": launch_app,
        "list_apps": list_apps,
        "get_screen_size": get_screen_size,
        "vision_click": vision_click,
        # Parallel multi-device tools
        "take_screenshots_parallel": take_screenshots_parallel,
        "list_elements_parallel": list_elements_parallel,
        "execute_parallel_actions": execute_parallel_actions,
    }


__all__ = ["create_autonomous_navigation_tools"]

