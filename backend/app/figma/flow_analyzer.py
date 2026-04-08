"""Figma Flow Analyzer — Clusters frames into visual flow groups and draws bounding boxes.

Multi-signal clustering:
1. SECTION nodes (if designer used Figma sections)
2. Prototype connections (connected components via transitionNodeID)
3. Name-prefix parsing (e.g. "Login / Screen 1", "Login / Screen 2")
4. Spatial clustering (Y-coordinate binning for horizontal rows)

Output: PIL image with colored bounding boxes around each detected flow group.
"""

from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)

# ── 10-color palette for flow groups (distinct, colorblind-friendly) ──────────
FLOW_COLORS: List[Tuple[int, int, int]] = [
    (30, 144, 255),   # Dodger blue
    (255, 99, 71),    # Tomato
    (50, 205, 50),    # Lime green
    (255, 165, 0),    # Orange
    (148, 103, 189),  # Purple
    (0, 206, 209),    # Dark turquoise
    (255, 20, 147),   # Deep pink
    (107, 142, 35),   # Olive drab
    (70, 130, 180),   # Steel blue
    (220, 20, 60),    # Crimson
]


@dataclass
class FigmaFrame:
    """A single frame/screen extracted from a Figma page."""
    node_id: str
    name: str
    x: float
    y: float
    width: float
    height: float
    # Optional: prototype target node IDs
    transition_targets: List[str] = field(default_factory=list)
    # Optional: parent section name
    section_name: Optional[str] = None


@dataclass
class FlowGroup:
    """A cluster of frames that form a single visual flow."""
    group_id: int
    name: str
    frames: List[FigmaFrame]
    color: Tuple[int, int, int] = (0, 255, 0)
    # Bounding box of the entire group (in canvas coords)
    bbox_x: float = 0
    bbox_y: float = 0
    bbox_w: float = 0
    bbox_h: float = 0

    def compute_bbox(self, padding: float = 40.0) -> None:
        """Compute bounding box that encloses all frames with padding."""
        if not self.frames:
            return
        min_x = min(f.x for f in self.frames)
        min_y = min(f.y for f in self.frames)
        max_x = max(f.x + f.width for f in self.frames)
        max_y = max(f.y + f.height for f in self.frames)
        self.bbox_x = min_x - padding
        self.bbox_y = min_y - padding
        self.bbox_w = (max_x - min_x) + 2 * padding
        self.bbox_h = (max_y - min_y) + 2 * padding


@dataclass
class FlowAnalysisResult:
    """Result of analyzing a Figma page for visual flows."""
    file_key: str
    page_name: str
    total_frames: int
    flow_groups: List[FlowGroup]
    clustering_method: str  # which signal was used
    visualization_path: str = ""


class FigmaFlowAnalyzer:
    """Analyzes Figma pages to detect and cluster visual flow groups."""

    # Y-gap threshold: frames > this apart vertically are different rows
    Y_GAP_THRESHOLD = 200.0
    # X-gap threshold for splitting within a row
    X_GAP_THRESHOLD = 600.0
    # Group padding for bounding box
    GROUP_PADDING = 50.0

    def __init__(self, access_token: Optional[str] = None):
        self._token = access_token
        self._client = None

    async def _get_client(self):
        """Lazy-init Figma client."""
        if self._client is None:
            from app.figma.client import FigmaClient
            if not self._token:
                raise ValueError("FIGMA_ACCESS_TOKEN required")
            self._client = FigmaClient(self._token)
        return self._client

    # ── Phase 1: Extract frames from Figma API ──────────────────────────

    async def extract_frames(self, file_key: str, page_name: Optional[str] = None) -> List[FigmaFrame]:
        """Extract all top-level FRAME nodes from a Figma file page."""
        client = await self._get_client()
        # depth=3 to reach FRAMEs inside SECTION nodes (DOC→CANVAS→SECTION→FRAME)
        data = await client.get_file(file_key, depth=3)
        document = data.get("document", {})
        pages = document.get("children", [])

        if not pages:
            raise ValueError("No pages found in Figma file")

        # Pick target page
        target_page = None
        if page_name:
            for p in pages:
                if p.get("name", "").lower() == page_name.lower():
                    target_page = p
                    break
            if not target_page:
                raise ValueError(f"Page '{page_name}' not found. Available: {[p['name'] for p in pages]}")
        else:
            target_page = pages[0]  # Default to first page

        logger.info(f"Analyzing page: {target_page.get('name', 'unnamed')}")
        return self._extract_frames_from_node(target_page)

    def _extract_frames_from_node(
        self, node: Dict[str, Any], parent_section: Optional[str] = None
    ) -> List[FigmaFrame]:
        """Recursively extract FRAME nodes, tracking parent sections."""
        frames: List[FigmaFrame] = []
        node_type = node.get("type", "")

        # Track sections for clustering signal
        current_section = parent_section
        if node_type == "SECTION":
            current_section = node.get("name", "Unnamed Section")

        # Extract frames (top-level design screens)
        if node_type == "FRAME" and "absoluteBoundingBox" in node:
            bbox = node["absoluteBoundingBox"]
            # Collect prototype transitions
            transitions = []
            for child in self._walk_children(node):
                if "transitionNodeID" in child:
                    transitions.append(child["transitionNodeID"])

            frames.append(FigmaFrame(
                node_id=node.get("id", ""),
                name=node.get("name", "Unnamed"),
                x=bbox.get("x", 0),
                y=bbox.get("y", 0),
                width=bbox.get("width", 0),
                height=bbox.get("height", 0),
                transition_targets=transitions,
                section_name=current_section,
            ))
        else:
            # Recurse into children (pages, sections, groups)
            for child in node.get("children", []):
                frames.extend(self._extract_frames_from_node(child, current_section))

        return frames

    def _walk_children(self, node: Dict[str, Any]):
        """Yield all descendants of a node."""
        for child in node.get("children", []):
            yield child
            yield from self._walk_children(child)


    # ── Phase 2: Multi-signal clustering ─────────────────────────────────

    def cluster_flows(self, frames: List[FigmaFrame]) -> Tuple[List[FlowGroup], str]:
        """Cluster frames into flow groups using priority cascade.

        Returns (groups, method_used).
        Priority:
          1. Section-based (if SECTION nodes exist)
          2. Prototype connections (connected components)
          3. Name-prefix parsing (e.g. "Login / Screen 1")
          4. Spatial clustering (Y-binning + X-gap splitting)
        """
        if not frames:
            return [], "none"

        # Signal 1: Sections
        sections = {f.section_name for f in frames if f.section_name}
        if sections and len(sections) >= 2:
            groups = self._cluster_by_section(frames)
            if len(groups) >= 2:
                return groups, "section"

        # Signal 2: Prototype connections
        groups = self._cluster_by_prototype(frames)
        if len(groups) >= 2:
            return groups, "prototype"

        # Signal 3: Name prefixes
        groups = self._cluster_by_name_prefix(frames)
        if len(groups) >= 2:
            return groups, "name_prefix"

        # Signal 4: Spatial (always works)
        groups = self._cluster_spatially(frames)
        return groups, "spatial"

    def _cluster_by_section(self, frames: List[FigmaFrame]) -> List[FlowGroup]:
        """Group frames by their parent SECTION node."""
        section_map: Dict[str, List[FigmaFrame]] = {}
        ungrouped: List[FigmaFrame] = []
        for f in frames:
            if f.section_name:
                section_map.setdefault(f.section_name, []).append(f)
            else:
                ungrouped.append(f)
        groups = []
        for i, (name, frs) in enumerate(section_map.items()):
            frs.sort(key=lambda f: f.x)  # L→R within section
            g = FlowGroup(group_id=i, name=name, frames=frs,
                          color=FLOW_COLORS[i % len(FLOW_COLORS)])
            g.compute_bbox(self.GROUP_PADDING)
            groups.append(g)
        if ungrouped:
            g = FlowGroup(group_id=len(groups), name="Ungrouped", frames=ungrouped,
                          color=(128, 128, 128))
            g.compute_bbox(self.GROUP_PADDING)
            groups.append(g)
        return groups

    def _cluster_by_prototype(self, frames: List[FigmaFrame]) -> List[FlowGroup]:
        """Connected components via prototype transitionNodeID links."""
        id_to_frame = {f.node_id: f for f in frames}
        parent: Dict[str, str] = {f.node_id: f.node_id for f in frames}

        def find(x: str) -> str:
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(a: str, b: str):
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[ra] = rb

        for f in frames:
            for target_id in f.transition_targets:
                if target_id in id_to_frame:
                    union(f.node_id, target_id)

        comp_map: Dict[str, List[FigmaFrame]] = {}
        for f in frames:
            root = find(f.node_id)
            comp_map.setdefault(root, []).append(f)

        # Only count components with >1 frame as real flows
        groups = []
        idx = 0
        for root, frs in comp_map.items():
            if len(frs) < 2:
                continue
            frs.sort(key=lambda f: f.x)
            g = FlowGroup(group_id=idx, name=self._infer_flow_name(frs),
                          frames=frs, color=FLOW_COLORS[idx % len(FLOW_COLORS)])
            g.compute_bbox(self.GROUP_PADDING)
            groups.append(g)
            idx += 1
        return groups

    def _cluster_by_name_prefix(self, frames: List[FigmaFrame]) -> List[FlowGroup]:
        """Group by common name prefix (e.g. 'Login / Screen 1' → prefix 'Login')."""
        prefix_map: Dict[str, List[FigmaFrame]] = {}
        for f in frames:
            prefix = self._extract_name_prefix(f.name)
            if prefix:
                prefix_map.setdefault(prefix, []).append(f)

        groups = []
        idx = 0
        used = set()
        for prefix, frs in prefix_map.items():
            if len(frs) < 2:
                continue
            frs.sort(key=lambda f: f.x)
            g = FlowGroup(group_id=idx, name=prefix, frames=frs,
                          color=FLOW_COLORS[idx % len(FLOW_COLORS)])
            g.compute_bbox(self.GROUP_PADDING)
            groups.append(g)
            used.update(f.node_id for f in frs)
            idx += 1
        return groups

    def _extract_name_prefix(self, name: str) -> Optional[str]:
        """Extract flow prefix from frame name.

        Patterns: 'Login / Step 1', 'Login - Home', 'Login_01', 'Onboarding 1'
        """
        # Try separator-based: "Prefix / Suffix" or "Prefix - Suffix"
        for sep in [" / ", " - ", " — ", "/"]:
            if sep in name:
                prefix = name.split(sep)[0].strip()
                if len(prefix) >= 2:
                    return prefix
        # Try trailing number: "Login01" → "Login"
        m = re.match(r'^(.+?)[\s_]*\d+$', name)
        if m:
            prefix = m.group(1).strip()
            if len(prefix) >= 2:
                return prefix
        return None

    def _cluster_spatially(self, frames: List[FigmaFrame]) -> List[FlowGroup]:
        """Cluster by Y-coordinate binning (horizontal rows) + X-gap splitting.

        Algorithm:
        1. Sort frames by Y coordinate (top → bottom)
        2. Bin into rows where consecutive Y-gap > Y_GAP_THRESHOLD
        3. Within each row, sort by X and split on X-gap > X_GAP_THRESHOLD
        """
        if not frames:
            return []

        sorted_frames = sorted(frames, key=lambda f: f.y)
        rows: List[List[FigmaFrame]] = [[sorted_frames[0]]]

        for f in sorted_frames[1:]:
            last_row = rows[-1]
            # Compare center-Y of current frame with center-Y of last frame in row
            last_cy = last_row[-1].y + last_row[-1].height / 2
            curr_cy = f.y + f.height / 2
            if abs(curr_cy - last_cy) > self.Y_GAP_THRESHOLD:
                rows.append([f])
            else:
                last_row.append(f)

        # Within each row, sort by X and split on large X gaps
        groups: List[FlowGroup] = []
        idx = 0
        for row in rows:
            row.sort(key=lambda f: f.x)
            sub_groups = self._split_row_by_x_gap(row)
            for sg in sub_groups:
                name = self._infer_flow_name(sg)
                g = FlowGroup(group_id=idx, name=name, frames=sg,
                              color=FLOW_COLORS[idx % len(FLOW_COLORS)])
                g.compute_bbox(self.GROUP_PADDING)
                groups.append(g)
                idx += 1

        return groups

    def _split_row_by_x_gap(self, row: List[FigmaFrame]) -> List[List[FigmaFrame]]:
        """Split a horizontal row of frames into sub-groups at large X gaps."""
        if len(row) <= 1:
            return [row]
        sub_groups: List[List[FigmaFrame]] = [[row[0]]]
        for f in row[1:]:
            prev = sub_groups[-1][-1]
            gap = f.x - (prev.x + prev.width)
            if gap > self.X_GAP_THRESHOLD:
                sub_groups.append([f])
            else:
                sub_groups[-1].append(f)
        return sub_groups

    def _infer_flow_name(self, frames: List[FigmaFrame]) -> str:
        """Infer a flow group name from frame names."""
        if not frames:
            return "Unknown Flow"

        # Try common prefix from name-prefix extraction
        prefixes = [self._extract_name_prefix(f.name) for f in frames]
        valid = [p for p in prefixes if p]
        if valid:
            from collections import Counter
            most_common = Counter(valid).most_common(1)[0][0]
            return most_common

        # Fallback: find longest common prefix of all names
        names = [f.name for f in frames]
        if len(names) == 1:
            return names[0]
        prefix = names[0]
        for n in names[1:]:
            while not n.startswith(prefix) and prefix:
                prefix = prefix[:-1]
        prefix = prefix.rstrip(" /-_")
        if len(prefix) >= 2:
            return prefix

        return f"Flow ({frames[0].name.split('/')[0].strip()}...)"

    # ── Phase 3: Visualization ───────────────────────────────────────────

    def visualize_flow_groups(
        self,
        flow_groups: List[FlowGroup],
        canvas_width: int = 4000,
        canvas_height: int = 3000,
        output_path: Optional[str] = None,
        page_image: Optional[Image.Image] = None,
    ) -> Image.Image:
        """Draw colored bounding boxes around flow groups on a canvas.

        If page_image is provided (from Figma render), draws on that.
        Otherwise creates a synthetic canvas showing frame rectangles.

        Args:
            flow_groups: Detected flow groups with computed bboxes.
            canvas_width: Width of output canvas (used if no page_image).
            canvas_height: Height of output canvas (used if no page_image).
            output_path: If set, saves the image to this path.
            page_image: Optional rendered Figma page to draw on.

        Returns:
            PIL Image with bounding boxes drawn.
        """
        # Compute canvas bounds from all frames
        all_frames = [f for g in flow_groups for f in g.frames]
        if not all_frames:
            raise ValueError("No frames to visualize")

        min_x = min(f.x for f in all_frames) - 100
        min_y = min(f.y for f in all_frames) - 100
        max_x = max(f.x + f.width for f in all_frames) + 100
        max_y = max(f.y + f.height for f in all_frames) + 100

        cw = max_x - min_x
        ch = max_y - min_y

        if page_image:
            # Scale page image to match canvas coordinate space
            img = page_image.copy()
            scale_x = img.width / cw
            scale_y = img.height / ch
        else:
            # Create synthetic canvas — scale to fit while keeping readable
            scale = min(canvas_width / cw, canvas_height / ch, 1.0)
            img_w = max(int(cw * scale), 800)
            img_h = max(int(ch * scale), 600)
            # If aspect ratio is too extreme (flat or tall), ensure min dimension
            if img_h < img_w // 3:
                # Too flat — scale height independently to fill canvas_height
                img_h = min(canvas_height, int(ch * (canvas_width / cw)))
                img_h = max(img_h, canvas_height // 2)
            if img_w < img_h // 3:
                # Too tall — scale width independently
                img_w = min(canvas_width, int(cw * (canvas_height / ch)))
                img_w = max(img_w, canvas_width // 2)
            img = Image.new("RGB", (img_w, img_h), (25, 25, 30))
            scale_x = img_w / cw
            scale_y = img_h / ch

        draw = ImageDraw.Draw(img)

        # Calculate font size based on image width
        font_size = max(16, img.width // 80)
        try:
            font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", font_size)
            font_small = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc",
                                           max(12, font_size * 2 // 3))
        except (OSError, IOError):
            font = ImageFont.load_default()
            font_small = font

        # Draw individual frames (if no page_image)
        if not page_image:
            for f in all_frames:
                fx = (f.x - min_x) * scale_x
                fy = (f.y - min_y) * scale_y
                fw = f.width * scale_x
                fh = f.height * scale_y
                # Frame rectangle (dark fill, light border)
                draw.rectangle([fx, fy, fx + fw, fy + fh],
                               fill=(40, 42, 48), outline=(80, 85, 95), width=2)
                # Frame name label (small, inside top)
                draw.text((fx + 8, fy + 6), f.name, fill=(160, 165, 175),
                          font=font_small)

        # Draw flow group bounding boxes
        line_w = max(3, img.width // 400)
        for g in flow_groups:
            bx = (g.bbox_x - min_x) * scale_x
            by = (g.bbox_y - min_y) * scale_y
            bw = g.bbox_w * scale_x
            bh = g.bbox_h * scale_y
            r, gc_, b = g.color

            # Draw rounded-corner-like box (4 separate rectangles for thick border)
            for offset in range(line_w):
                draw.rectangle(
                    [bx + offset, by + offset, bx + bw - offset, by + bh - offset],
                    outline=(r, gc_, b, 200),
                )

            # Semi-transparent fill overlay (approximate with repeated thin lines)
            # Just draw a subtle fill band at top for the label
            label_h = font_size + 16
            draw.rectangle([bx, by, bx + bw, by + label_h],
                           fill=(r, gc_, b))

            # Group label
            label = f"  {g.name}  ({len(g.frames)} screens)"
            draw.text((bx + 10, by + 6), label, fill=(255, 255, 255), font=font)

            # Draw arrow connectors between frames within group
            sorted_frames = sorted(g.frames, key=lambda f: f.x)
            for i in range(len(sorted_frames) - 1):
                fa = sorted_frames[i]
                fb = sorted_frames[i + 1]
                ax = (fa.x + fa.width - min_x) * scale_x
                ay = (fa.y + fa.height / 2 - min_y) * scale_y
                bx_arrow = (fb.x - min_x) * scale_x
                by_arrow = (fb.y + fb.height / 2 - min_y) * scale_y
                draw.line([ax, ay, bx_arrow, by_arrow],
                          fill=(r, gc_, b, 180), width=max(2, line_w - 1))
                # Arrowhead
                self._draw_arrowhead(draw, ax, ay, bx_arrow, by_arrow,
                                     (r, gc_, b), size=12)

        if output_path:
            img.save(output_path)
            logger.info(f"Flow visualization saved to {output_path}")

        return img

    @staticmethod
    def _draw_arrowhead(draw: ImageDraw.Draw, x1: float, y1: float,
                        x2: float, y2: float, color: Tuple[int, int, int],
                        size: int = 10):
        """Draw a small arrowhead at (x2, y2) pointing from (x1, y1)."""
        angle = math.atan2(y2 - y1, x2 - x1)
        # Two sides of the arrowhead
        a1 = angle + math.pi * 0.85
        a2 = angle - math.pi * 0.85
        points = [
            (x2, y2),
            (x2 + size * math.cos(a1), y2 + size * math.sin(a1)),
            (x2 + size * math.cos(a2), y2 + size * math.sin(a2)),
        ]
        draw.polygon(points, fill=color)

    # ── High-level API ───────────────────────────────────────────────────

    async def analyze(
        self,
        file_key: str,
        page_name: Optional[str] = None,
        output_path: Optional[str] = None,
    ) -> FlowAnalysisResult:
        """Full pipeline: extract → cluster → visualize.

        Args:
            file_key: Figma file key.
            page_name: Target page name (default: first page).
            output_path: Path to save visualization image.

        Returns:
            FlowAnalysisResult with detected groups and visualization path.
        """
        # Phase 1: Extract
        frames = await self.extract_frames(file_key, page_name)
        logger.info(f"Extracted {len(frames)} frames")

        # Phase 2: Cluster
        groups, method = self.cluster_flows(frames)
        logger.info(f"Clustered into {len(groups)} groups using '{method}' method")

        # Phase 3: Visualize
        out = output_path or "figma_flow_groups.png"
        self.visualize_flow_groups(groups, output_path=out)

        return FlowAnalysisResult(
            file_key=file_key,
            page_name=page_name or "Page 1",
            total_frames=len(frames),
            flow_groups=groups,
            clustering_method=method,
            visualization_path=out,
        )

    def analyze_frames_direct(
        self,
        frames: List[FigmaFrame],
        output_path: Optional[str] = None,
    ) -> FlowAnalysisResult:
        """Analyze pre-extracted frames (useful for testing without API).

        Args:
            frames: List of FigmaFrame objects.
            output_path: Path to save visualization image.

        Returns:
            FlowAnalysisResult with detected groups.
        """
        groups, method = self.cluster_flows(frames)
        out = output_path or "figma_flow_groups.png"
        self.visualize_flow_groups(groups, output_path=out)
        return FlowAnalysisResult(
            file_key="local",
            page_name="Local Analysis",
            total_frames=len(frames),
            flow_groups=groups,
            clustering_method=method,
            visualization_path=out,
        )


# ── Demo / Test Data ─────────────────────────────────────────────────────

def create_demo_figma_layout() -> List[FigmaFrame]:
    """Create a realistic multi-flow Figma page layout for testing.

    Simulates a real design file with 5 distinct flows arranged as horizontal rows:
    - Row 1: Login Flow (4 screens)
    - Row 2: Onboarding Flow (5 screens)
    - Row 3: Dashboard Flow (3 screens) + Settings Flow (3 screens)  ← same Y, split by X-gap
    - Row 4: Checkout Flow (6 screens)
    """
    SCREEN_W, SCREEN_H = 375, 812  # Standard mobile frame
    GAP_X = 100   # gap between screens in same flow
    GAP_Y = 300   # gap between rows
    FLOW_GAP_X = 800  # gap between different flows on same row

    frames: List[FigmaFrame] = []
    y_cursor = 0

    # ── Row 1: Login Flow ────────────────────────────────────────────
    login_screens = [
        "Login / Welcome", "Login / Email Input", "Login / Password",
        "Login / 2FA Verify",
    ]
    x = 0
    for name in login_screens:
        frames.append(FigmaFrame(
            node_id=f"login_{len(frames)}", name=name,
            x=x, y=y_cursor, width=SCREEN_W, height=SCREEN_H,
        ))
        x += SCREEN_W + GAP_X

    # ── Row 2: Onboarding Flow ───────────────────────────────────────
    y_cursor += SCREEN_H + GAP_Y
    onboarding_screens = [
        "Onboarding / Welcome", "Onboarding / Feature 1",
        "Onboarding / Feature 2", "Onboarding / Permissions",
        "Onboarding / Complete",
    ]
    x = 0
    for name in onboarding_screens:
        frames.append(FigmaFrame(
            node_id=f"onboard_{len(frames)}", name=name,
            x=x, y=y_cursor, width=SCREEN_W, height=SCREEN_H,
        ))
        x += SCREEN_W + GAP_X

    # ── Row 3: Dashboard (3) + Settings (3) on SAME Y ───────────────
    y_cursor += SCREEN_H + GAP_Y
    dash_screens = ["Dashboard / Home", "Dashboard / Analytics", "Dashboard / Reports"]
    x = 0
    for name in dash_screens:
        frames.append(FigmaFrame(
            node_id=f"dash_{len(frames)}", name=name,
            x=x, y=y_cursor, width=SCREEN_W, height=SCREEN_H,
        ))
        x += SCREEN_W + GAP_X

    x += FLOW_GAP_X  # Large gap before Settings flow
    settings_screens = ["Settings / Profile", "Settings / Notifications", "Settings / Privacy"]
    for name in settings_screens:
        frames.append(FigmaFrame(
            node_id=f"settings_{len(frames)}", name=name,
            x=x, y=y_cursor, width=SCREEN_W, height=SCREEN_H,
        ))
        x += SCREEN_W + GAP_X

    # ── Row 4: Checkout Flow ─────────────────────────────────────────
    y_cursor += SCREEN_H + GAP_Y
    checkout_screens = [
        "Checkout / Cart", "Checkout / Shipping", "Checkout / Payment",
        "Checkout / Review", "Checkout / Confirm", "Checkout / Success",
    ]
    x = 0
    for name in checkout_screens:
        frames.append(FigmaFrame(
            node_id=f"checkout_{len(frames)}", name=name,
            x=x, y=y_cursor, width=SCREEN_W, height=SCREEN_H,
        ))
        x += SCREEN_W + GAP_X

    return frames