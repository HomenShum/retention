#!/usr/bin/env python3
"""Generate Figma flow overlay using direct computer vision (no coordinate mapping)."""
from PIL import Image, ImageDraw, ImageFont
import numpy as np
from scipy import ndimage
from scipy.ndimage import binary_closing, binary_opening
import os

SRC = "screenshots/figma_flows/figma_cv_clean.png"
OUT_FULL = "screenshots/figma_flows/figma_overlay_cv_direct.png"
OUT_CROP = "screenshots/figma_flows/figma_overlay_cv_cropped.png"

FLOW_GROUPS = [
    ("Scapes", (255, 99, 71)),
    ("Onboarding Flows", (255, 105, 180)),
    ("Launch Flows", (30, 144, 255)),
    ("Browse Experience", (255, 165, 0)),
    ("Creator", (147, 112, 219)),
    ("Library Experience", (0, 206, 209)),
    ("Product Display Page", (50, 205, 50)),
]

def detect_subframes(arr_region, sec_x, sec_y):
    """Detect individual frames within a section using column brightness gaps."""
    brightness = arr_region.mean(axis=2)
    col_bright = brightness.mean(axis=0)
    # Find dark columns (gaps between frames)
    dark_cols = col_bright < 50
    # Find runs of dark columns that are wide enough to be inter-frame gaps
    subframes = []
    in_content = False
    content_start = 0
    for c in range(len(col_bright)):
        if not dark_cols[c] and not in_content:
            content_start = c
            in_content = True
        elif dark_cols[c] and in_content:
            w = c - content_start
            if w > 15:  # Minimum sub-frame width
                # Find vertical extent of this sub-frame
                col_slice = brightness[:, content_start:c]
                row_bright = col_slice.mean(axis=1)
                bright_rows = np.where(row_bright > 50)[0]
                if len(bright_rows) > 10:
                    y0, y1 = int(bright_rows[0]), int(bright_rows[-1])
                    subframes.append(dict(
                        x=sec_x + content_start, y=sec_y + y0,
                        w=w, h=y1 - y0))
            in_content = False
    # Handle last content run
    if in_content:
        w = len(col_bright) - content_start
        if w > 15:
            col_slice = brightness[:, content_start:]
            row_bright = col_slice.mean(axis=1)
            bright_rows = np.where(row_bright > 50)[0]
            if len(bright_rows) > 10:
                y0, y1 = int(bright_rows[0]), int(bright_rows[-1])
                subframes.append(dict(
                    x=sec_x + content_start, y=sec_y + y0,
                    w=w, h=y1 - y0))
    return subframes


def main():
    img = Image.open(SRC).convert("RGBA")
    arr = np.array(img)[:, :, :3]
    W, H = img.width, img.height
    print(f"Image: {W}x{H}")

    brightness = arr.mean(axis=2)

    # --- Section-level groups (threshold=80, heavier closing) ---
    sec_mask = brightness > 80
    sec_closed = binary_closing(sec_mask, structure=np.ones((7, 7)), iterations=3)
    sec_clean = binary_opening(sec_closed, structure=np.ones((5, 5)), iterations=1)
    labeled_sec, n_sec = ndimage.label(sec_clean)

    sections = []
    for i in range(1, n_sec + 1):
        ys, xs = np.where(labeled_sec == i)
        area = len(ys)
        x0, x1_ = int(xs.min()), int(xs.max())
        y0, y1_ = int(ys.min()), int(ys.max())
        w, h = x1_ - x0, y1_ - y0
        if area > 2000 and w > 40 and h > 40 and y0 > 40 and y1_ < (H - 30):
            sections.append(dict(x=x0, y=y0, w=w, h=h, area=area, frames=[]))
    sections.sort(key=lambda s: s["x"])
    print(f"Section groups: {len(sections)}")

    # --- Detect individual frames within each section via column brightness ---
    total_frames = 0
    for s in sections:
        region = arr[s["y"]:s["y"]+s["h"], s["x"]:s["x"]+s["w"], :]
        s["frames"] = detect_subframes(region, s["x"], s["y"])
        total_frames += len(s["frames"])

    print(f"Total sub-frames detected: {total_frames}")
    for i, s in enumerate(sections):
        print(f"  Sec {i}: ({s['x']},{s['y']})-({s['x']+s['w']},{s['y']+s['h']}) "
              f"{s['w']}x{s['h']} -> {len(s['frames'])} frames")

    # --- Draw overlay ---
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    try:
        font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 22)
        sfont = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 14)
        tfont = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 11)
    except Exception:
        font = sfont = tfont = ImageFont.load_default()

    pad = 10
    for i, (s, (name, color)) in enumerate(zip(sections[:7], FLOW_GROUPS)):
        bx1, by1 = max(0, s["x"] - pad), max(0, s["y"] - pad)
        bx2, by2 = min(W, s["x"] + s["w"] + pad), min(H, s["y"] + s["h"] + pad)
        draw.rectangle([bx1, by1, bx2, by2], fill=color + (30,), outline=color + (200,), width=3)

        label = name
        bb = draw.textbbox((0, 0), label, font=font)
        tw, th = bb[2] - bb[0], bb[3] - bb[1]
        ly = by1 - th - 14 if by1 - th - 14 > 5 else by2 + 4
        draw.rounded_rectangle([bx1, ly, bx1 + tw + 14, ly + th + 8], radius=4, fill=color + (220,))
        draw.text((bx1 + 7, ly + 3), label, fill=(255, 255, 255, 255), font=font)
        n = len(s["frames"])
        draw.text((bx1 + tw + 20, ly + 6), f"{n} screen{'s' if n != 1 else ''}", fill=color + (180,), font=sfont)

        for j, f in enumerate(s["frames"]):
            lc = tuple(min(255, c + 80) for c in color) + (150,)
            draw.rectangle([f["x"]-2, f["y"]-2, f["x"]+f["w"]+2, f["y"]+f["h"]+2], outline=lc, width=2)
            draw.text((f["x"]+3, f["y"]+3), f"#{j+1}", fill=(255,255,255,200), font=tfont)

    result = Image.alpha_composite(img, overlay)

    # Legend
    dr = ImageDraw.Draw(result)
    lx, ly_ = W - 310, 55
    dr.rounded_rectangle([lx-8, ly_-8, lx+300, ly_+len(FLOW_GROUPS)*28+14], radius=6, fill=(15,15,15,210))
    dr.text((lx, ly_), "FLOW GROUPS", fill=(255,255,255), font=sfont)
    for i, (nm, c) in enumerate(FLOW_GROUPS):
        ry = ly_ + 22 + i * 26
        dr.rectangle([lx, ry+2, lx+14, ry+16], fill=c)
        dr.text((lx+22, ry), nm, fill=(220,220,220), font=sfont)

    result.convert("RGB").save(OUT_FULL)
    print(f"\nFull: {OUT_FULL} ({W}x{H}, {os.path.getsize(OUT_FULL)/1024:.1f} KB)")

    # Cropped 2x
    ay1 = min(s["y"] for s in sections[:7]) - 50
    ay2 = max(s["y"] + s["h"] for s in sections[:7]) + 25
    ax1 = max(0, min(s["x"] for s in sections[:7]) - 20)
    ax2 = min(W, max(s["x"] + s["w"] for s in sections[:7]) + 20)
    cr = result.convert("RGB").crop((ax1, ay1, ax2, ay2))
    cr2 = cr.resize((cr.width * 2, cr.height * 2), Image.LANCZOS)
    cr2.save(OUT_CROP)
    print(f"Cropped 2x: {OUT_CROP} ({cr2.width}x{cr2.height}, {os.path.getsize(OUT_CROP)/1024:.1f} KB)")
    print(f"\nView:\n  http://127.0.0.1:8899/figma_overlay_cv_direct.png\n  http://127.0.0.1:8899/figma_overlay_cv_cropped.png")

if __name__ == "__main__":
    main()

