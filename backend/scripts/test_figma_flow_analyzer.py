#!/usr/bin/env python3
"""Test the Figma Flow Analyzer with demo data and real bounding box output.

Usage:
    python scripts/test_figma_flow_analyzer.py

    # With real Figma file:
    FIGMA_ACCESS_TOKEN=xxx python scripts/test_figma_flow_analyzer.py --file-key YOUR_KEY
"""

import argparse
import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.figma.flow_analyzer import (
    FigmaFlowAnalyzer,
    create_demo_figma_layout,
)

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "..", "screenshots", "figma_flows")


def run_demo():
    """Run the analyzer on demo data (no Figma API needed)."""
    print("=" * 70)
    print("  FIGMA FLOW ANALYZER — Demo Mode")
    print("=" * 70)
    print()

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # Create demo layout
    frames = create_demo_figma_layout()
    print(f"📐 Created demo layout: {len(frames)} frames")
    print()

    # Show raw frame positions
    print("  Frame Layout:")
    for f in frames:
        print(f"    [{f.name:35s}]  x={f.x:6.0f}  y={f.y:6.0f}  "
              f"w={f.width:.0f}×{f.height:.0f}")
    print()

    # Run analyzer
    analyzer = FigmaFlowAnalyzer()
    t0 = time.time()
    result = analyzer.analyze_frames_direct(
        frames,
        output_path=os.path.join(OUTPUT_DIR, "flow_groups_demo.png"),
    )
    elapsed = time.time() - t0

    # Print results
    print(f"🔍 Clustering method: {result.clustering_method}")
    print(f"📊 Detected {len(result.flow_groups)} flow groups:")
    print()
    for g in result.flow_groups:
        color_hex = f"#{g.color[0]:02x}{g.color[1]:02x}{g.color[2]:02x}"
        print(f"  ┌─ Group {g.group_id}: \"{g.name}\" ({color_hex})")
        print(f"  │  Screens: {len(g.frames)}")
        print(f"  │  Bbox: ({g.bbox_x:.0f}, {g.bbox_y:.0f}) "
              f"→ ({g.bbox_x + g.bbox_w:.0f}, {g.bbox_y + g.bbox_h:.0f})")
        for f in g.frames:
            print(f"  │    → {f.name}")
        print(f"  └─")
        print()

    print(f"⏱️  Analysis time: {elapsed * 1000:.1f}ms")
    print(f"🖼️  Visualization saved: {result.visualization_path}")

    # File size
    if os.path.exists(result.visualization_path):
        size = os.path.getsize(result.visualization_path)
        print(f"   File size: {size / 1024:.1f} KB")

    return result


async def run_live(file_key: str, page_name: str = None):
    """Run the analyzer on a real Figma file."""
    token = os.environ.get("FIGMA_ACCESS_TOKEN")
    if not token:
        print("❌ FIGMA_ACCESS_TOKEN not set. Use --demo or set the token.")
        return None

    print("=" * 70)
    print(f"  FIGMA FLOW ANALYZER — Live Mode (file: {file_key})")
    print("=" * 70)
    print()

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    analyzer = FigmaFlowAnalyzer(access_token=token)
    t0 = time.time()
    result = await analyzer.analyze(
        file_key=file_key,
        page_name=page_name,
        output_path=os.path.join(OUTPUT_DIR, f"flow_groups_{file_key}.png"),
    )
    elapsed = time.time() - t0

    print(f"📐 Extracted {result.total_frames} frames from page '{result.page_name}'")
    print(f"🔍 Clustering method: {result.clustering_method}")
    print(f"📊 Detected {len(result.flow_groups)} flow groups:")
    for g in result.flow_groups:
        print(f"  • {g.name} ({len(g.frames)} screens)")
    print(f"⏱️  Analysis time: {elapsed:.1f}s")
    print(f"🖼️  Saved: {result.visualization_path}")

    return result


def main():
    parser = argparse.ArgumentParser(description="Figma Flow Analyzer")
    parser.add_argument("--file-key", help="Figma file key for live mode")
    parser.add_argument("--page", help="Target page name (default: first page)")
    parser.add_argument("--demo", action="store_true", default=True,
                        help="Run with demo data (default)")
    args = parser.parse_args()

    if args.file_key:
        asyncio.run(run_live(args.file_key, args.page))
    else:
        run_demo()


if __name__ == "__main__":
    main()

