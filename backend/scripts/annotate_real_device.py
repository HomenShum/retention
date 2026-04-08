"""Annotate a REAL device screenshot using the actual SoM pipeline."""
import json, importlib.util, sys
from PIL import Image, ImageDraw

# Load the real annotation module
spec = importlib.util.spec_from_file_location(
    "mod", "app/agents/device_testing/tools/autonomous_navigation_tools.py"
)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

# Real elements from emulator-5554 (Pixel 8 API 36) - Settings app
REAL_ELEMENTS = json.loads(open("/tmp/real_elements.json").read())

# Load real screenshot
img = Image.open("/tmp/real_device_raw.png").convert("RGBA")
print(f"Real screenshot: {img.width}x{img.height}")

base_draw = ImageDraw.Draw(img)
overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
overlay_draw = ImageDraw.Draw(overlay)

label_font_size = max(16, img.width // 54)
small_font_size = max(12, img.width // 72)
font = mod._load_font(label_font_size)
small_font = mod._load_font(small_font_size)
box_width = max(2, img.width // 360)

# Convert MCP element format to flat format for the pipeline
flat_elements = []
for elem in REAL_ELEMENTS:
    coords = elem.get("coordinates", {})
    flat = {
        "class": elem.get("type", ""),
        "text": elem.get("text", ""),
        "label": elem.get("label", ""),
        "resource_id": elem.get("identifier", ""),
        "x": coords.get("x", 0),
        "y": coords.get("y", 0),
        "width": coords.get("width", 0),
        "height": coords.get("height", 0),
        "clickable": False,
        "focusable": False,
    }
    flat_elements.append(flat)

sorted_elements = sorted(flat_elements, key=mod._element_sort_key)
drawn, placed_labels, type_counts = 0, [], {}

for elem in sorted_elements:
    x = int(elem.get("x", 0))
    yp = int(elem.get("y", 0))
    w = int(elem.get("width", 0))
    h = int(elem.get("height", 0))
    if w <= 0 or h <= 0:
        continue
    interactive = mod._is_interactive(elem)
    area = w * h
    if interactive and area < 100:
        continue
    if not interactive and area < 400:
        continue
    etype = mod._classify_element_type(elem)
    if etype == "container":
        continue
    raw_label = (elem.get("text") or "").strip()
    if not raw_label:
        raw_label = (elem.get("label") or "").strip()
    if not raw_label:
        raw_label = (elem.get("content_desc") or "").strip()
    if not raw_label:
        rid = elem.get("resource_id") or ""
        if rid:
            raw_label = rid.split("/")[-1].replace("_", " ")
    if not raw_label:
        continue
    raw_label = mod._bbox_clean_label(raw_label)
    if not raw_label:
        continue
    border_color, fill_color, tag = mod._ELEMENT_PALETTE.get(
        etype, mod._ELEMENT_PALETTE["unknown"]
    )
    display_idx = drawn + 1
    short = mod._bbox_ellipsize(raw_label, 30)
    label = f"#{display_idx} [{tag}]\n{short}"
    x1, y1, x2, y2 = x, yp, x + w, yp + h
    base_draw.rectangle([x1, y1, x2, y2], outline=border_color, width=box_width)
    text_w, text_h = mod._bbox_measure_multiline(
        overlay_draw, label, small_font, spacing=2
    )
    pad_x, pad_y = 6, 4
    bg_w, bg_h = text_w + pad_x * 2, text_h + pad_y * 2
    bg_x1, bg_y1, placed_rect = mod._bbox_find_label_position(
        box=(x1, y1, x2, y2),
        label_size=(bg_w, bg_h),
        image_size=(img.width, img.height),
        placed=placed_labels,
        margin=2,
    )
    placed_labels.append(placed_rect)
    bg_x2, bg_y2 = bg_x1 + bg_w, bg_y1 + bg_h
    bg_outline = border_color[:3] + (220,)
    if hasattr(overlay_draw, "rounded_rectangle"):
        overlay_draw.rounded_rectangle(
            [bg_x1, bg_y1, bg_x2, bg_y2],
            radius=6, fill=(0, 0, 0, 190), outline=bg_outline, width=1,
        )
    else:
        overlay_draw.rectangle(
            [bg_x1, bg_y1, bg_x2, bg_y2],
            fill=(0, 0, 0, 190), outline=bg_outline, width=1,
        )
    overlay_draw.multiline_text(
        (bg_x1 + pad_x, bg_y1 + pad_y),
        label, fill=(255, 255, 255, 255), font=small_font, spacing=2,
    )
    drawn += 1
    type_counts[tag] = type_counts.get(tag, 0) + 1
    if drawn >= 40:
        break

if drawn > 0:
    img = Image.alpha_composite(img, overlay)
    legend_overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    legend_draw = ImageDraw.Draw(legend_overlay)
    legend_font = mod._load_font(max(12, img.width // 80))
    legend_items = [f"{t}:{c}" for t, c in sorted(type_counts.items())]
    legend_text = "  ".join(legend_items) + f"  TOTAL:{drawn}"
    tw, th = mod._bbox_measure_multiline(legend_draw, legend_text, legend_font)
    ly = img.height - th - 12
    legend_draw.rectangle([0, ly - 6, tw + 20, img.height], fill=(0, 0, 0, 200))
    legend_draw.text((8, ly), legend_text, fill=(255, 255, 255, 240), font=legend_font)
    img = Image.alpha_composite(img, legend_overlay)

img.save("/tmp/real_device_annotated_SoM.png")
print(f"Annotated: {drawn} elements, types: {type_counts}")
print(f"Saved: /tmp/real_device_annotated_SoM.png")

