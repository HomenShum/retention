"""Generate before/after screenshot annotation demo images."""
import io, sys, os, importlib.util
from PIL import Image, ImageDraw, ImageFont

W, H = 1080, 2400

def draw_phone_ui():
    """Create a realistic Settings screen."""
    img = Image.new("RGB", (W, H), (250, 250, 250))
    d = ImageDraw.Draw(img)
    # Status bar
    d.rectangle([0, 0, W, 80], fill=(30, 30, 30))
    d.text((40, 25), "12:34", fill="white"); d.text((W-120, 25), "100%", fill="white")
    # App bar
    d.rectangle([0, 80, W, 220], fill=(63, 81, 181))
    d.text((40, 130), "Settings", fill="white")
    # Search bar
    d.rounded_rectangle([40, 260, W-40, 340], radius=20, fill="white", outline=(200,200,200))
    d.text((80, 285), "Search settings...", fill=(180,180,180))
    # Profile
    d.ellipse([40, 430, 160, 550], fill=(200,200,200))
    d.text((65, 475), "JD", fill=(100,100,100))
    d.text((190, 450), "John Doe", fill=(30,30,30))
    d.text((190, 490), "john@example.com", fill=(120,120,120))
    d.rounded_rectangle([W-250, 460, W-40, 520], radius=8, fill=(63,81,181))
    d.text((W-220, 478), "Edit Profile", fill="white")
    d.line([(40,580),(W-40,580)], fill=(220,220,220), width=2)
    # Toggles
    for label, on, y in [("Dark Mode",True,640),("Notifications",True,740),("Location Services",False,840),("Auto-Update",True,940)]:
        d.text((40, y+10), label, fill=(50,50,50))
        bg = (76,175,80) if on else (200,200,200)
        d.rounded_rectangle([W-140, y+5, W-40, y+55], radius=25, fill=bg)
        cx = W-65 if on else W-115
        d.ellipse([cx-20, y+10, cx+20, y+50], fill="white")
    # Radio buttons
    d.text((40, 1060), "Preferences", fill=(63,81,181))
    for i, lang in enumerate(["English","Spanish","French","German"]):
        y = 1120 + i*80
        sel = (i==0)
        d.ellipse([60,y+5,100,y+45], outline=(63,81,181) if sel else (180,180,180), width=3)
        if sel: d.ellipse([70,y+15,90,y+35], fill=(63,81,181))
        d.text((130, y+10), lang, fill=(50,50,50))
    # Text input
    d.text((40, 1460), "Display Name", fill=(100,100,100))
    d.rectangle([40, 1500, W-40, 1580], outline=(63,81,181), width=2)
    d.text((60, 1525), "John Doe", fill=(30,30,30))
    # Checkbox
    d.rectangle([40,1640,90,1690], outline=(63,81,181), width=2)
    d.line([(50,1665),(60,1680),(80,1650)], fill=(63,81,181), width=3)
    d.text((110, 1650), "Remember my preferences", fill=(50,50,50))
    # Buttons
    d.rounded_rectangle([40, 1760, W-40, 1860], radius=12, fill=(63,81,181))
    d.text((W//2-80, 1795), "Save Changes", fill="white")
    d.rounded_rectangle([40, 1900, W-40, 1980], radius=12, fill="white", outline=(63,81,181), width=2)
    d.text((W//2-60, 1925), "Cancel", fill=(63,81,181))
    # List items
    for i in range(3):
        y = 2040 + i*80
        d.rectangle([40,y,W-40,y+70], fill="white", outline=(240,240,240))
        d.text((60, y+20), f"List item {i+1}", fill=(50,50,50))
    # Bottom nav
    d.rectangle([0, H-120, W, H], fill="white")
    d.line([(0,H-120),(W,H-120)], fill=(220,220,220))
    for lbl, x in [("Home",180),("Search",420),("Profile",660),("Settings",900)]:
        d.text((x-30, H-80), lbl, fill=(63,81,181) if lbl=="Settings" else (150,150,150))
    return img

def draw_old_style(img):
    """Old annotation: all green, tiny 12px font, no type info."""
    out = img.copy()
    d = ImageDraw.Draw(out)
    try: font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 12)
    except: font = ImageFont.load_default()
    G = (0, 255, 0)
    elems = [
        (40,260,W-40,340,"Search settings..."),(40,430,160,550,"JD"),
        (W-250,460,W-40,520,"Edit Profile"),(W-140,645,W-40,695,"Dark Mode"),
        (W-140,745,W-40,795,"Notifications"),(W-140,845,W-40,895,"Location Svc"),
        (W-140,945,W-40,995,"Auto-Update"),(60,1125,100,1165,"English"),
        (60,1205,100,1245,"Spanish"),(40,1500,W-40,1580,"John Doe"),
        (40,1640,90,1690,"Remember pref"),(40,1760,W-40,1860,"Save Changes"),
        (40,1900,W-40,1980,"Cancel"),(40,2040,W-40,2110,"List item 1"),
        (40,2120,W-40,2190,"List item 2"),(40,2200,W-40,2270,"List item 3"),
        (150,H-80,230,H-40,"Home"),(390,H-80,470,H-40,"Search"),
        (630,H-80,710,H-40,"Profile"),(870,H-80,950,H-40,"Settings"),
    ]
    for i,(x1,y1,x2,y2,lbl) in enumerate(elems):
        d.rectangle([x1,y1,x2,y2], outline=G, width=2)
        d.text((x1, y1-14), f"{i+1}. {lbl[:20]}", fill=G, font=font)
    return out

if __name__ == "__main__":
    print("Generating phone UI...")
    base = draw_phone_ui()
    base.save("/tmp/phone_screen_base.png")

    print("Generating OLD annotation (mono green, 12px)...")
    old = draw_old_style(base)
    old.save("/tmp/phone_screen_OLD.png")
    print(f"  Saved /tmp/phone_screen_OLD.png")

    print("Generating NEW SoM annotation (real pipeline)...")
    spec = importlib.util.spec_from_file_location("mod", "app/agents/device_testing/tools/autonomous_navigation_tools.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    adb_elements = [
        {"class":"android.widget.SearchView","text":"Search settings...","focusable":True,"x":40,"y":260,"width":W-80,"height":80},
        {"class":"android.widget.ImageView","content_desc":"Profile photo","x":40,"y":430,"width":120,"height":120},
        {"class":"android.widget.TextView","text":"John Doe","x":190,"y":450,"width":300,"height":40},
        {"class":"android.widget.TextView","text":"john@example.com","x":190,"y":490,"width":300,"height":30},
        {"class":"android.widget.Button","text":"Edit Profile","clickable":True,"x":W-250,"y":460,"width":210,"height":60},
        {"class":"android.widget.Switch","text":"Dark Mode","checkable":True,"x":W-140,"y":645,"width":100,"height":50},
        {"class":"android.widget.Switch","text":"Notifications","checkable":True,"x":W-140,"y":745,"width":100,"height":50},
        {"class":"android.widget.Switch","text":"Location Services","checkable":True,"x":W-140,"y":845,"width":100,"height":50},
        {"class":"android.widget.Switch","text":"Auto-Update","checkable":True,"x":W-140,"y":945,"width":100,"height":50},
        {"class":"android.widget.RadioButton","text":"English","checkable":True,"x":60,"y":1125,"width":300,"height":50},
        {"class":"android.widget.RadioButton","text":"Spanish","checkable":True,"x":60,"y":1205,"width":300,"height":50},
        {"class":"android.widget.RadioButton","text":"French","checkable":True,"x":60,"y":1285,"width":300,"height":50},
        {"class":"android.widget.RadioButton","text":"German","checkable":True,"x":60,"y":1365,"width":300,"height":50},
        {"class":"android.widget.EditText","text":"John Doe","focusable":True,"x":40,"y":1500,"width":W-80,"height":80},
        {"class":"android.widget.CheckBox","text":"Remember my preferences","checkable":True,"x":40,"y":1640,"width":600,"height":50},
        {"class":"android.widget.Button","text":"Save Changes","clickable":True,"x":40,"y":1760,"width":W-80,"height":100},
        {"class":"android.widget.Button","text":"Cancel","clickable":True,"x":40,"y":1900,"width":W-80,"height":80},
        {"class":"android.widget.ListView","text":"","x":40,"y":2040,"width":W-80,"height":230},
        {"class":"com.google.android.material.bottomnavigation.BottomNavigationView","text":"","clickable":True,"x":0,"y":H-120,"width":W,"height":120},
        {"class":"android.widget.FrameLayout","text":"","x":0,"y":0,"width":W,"height":H},
    ]

    new_img = base.copy().convert("RGBA")
    base_draw = ImageDraw.Draw(new_img)
    overlay = Image.new("RGBA", new_img.size, (0,0,0,0))
    overlay_draw = ImageDraw.Draw(overlay)
    font = mod._load_font(max(16, new_img.width // 54))
    small_font = mod._load_font(max(12, new_img.width // 72))
    box_width = max(2, new_img.width // 360)
    sorted_elements = sorted(adb_elements, key=mod._element_sort_key)
    drawn, placed_labels, type_counts = 0, [], {}

    for elem in sorted_elements:
        x = int(elem.get("x",0)); yp = int(elem.get("y",0))
        w = int(elem.get("width",0)); h = int(elem.get("height",0))
        if w <= 0 or h <= 0: continue
        interactive = mod._is_interactive(elem)
        area = w * h
        if interactive and area < 100: continue
        if not interactive and area < 400: continue
        etype = mod._classify_element_type(elem)
        if etype == "container": continue
        raw_label = (elem.get("text") or "").strip()
        if not raw_label: raw_label = (elem.get("content_desc") or "").strip()
        if not raw_label: continue
        raw_label = mod._bbox_clean_label(raw_label)
        if not raw_label: continue
        border_color, fill_color, tag = mod._ELEMENT_PALETTE.get(etype, mod._ELEMENT_PALETTE["unknown"])
        display_idx = drawn + 1
        short = mod._bbox_ellipsize(raw_label, 30)
        label = f"#{display_idx} [{tag}]\n{short}"
        x1, y1, x2, y2 = x, yp, x+w, yp+h
        base_draw.rectangle([x1,y1,x2,y2], outline=border_color, width=box_width)
        text_w, text_h = mod._bbox_measure_multiline(overlay_draw, label, small_font, spacing=2)
        pad_x, pad_y = 6, 4
        bg_w, bg_h = text_w + pad_x*2, text_h + pad_y*2
        bg_x1, bg_y1, placed_rect = mod._bbox_find_label_position(
            box=(x1,y1,x2,y2), label_size=(bg_w,bg_h),
            image_size=(new_img.width, new_img.height), placed=placed_labels, margin=2)
        placed_labels.append(placed_rect)
        bg_x2, bg_y2 = bg_x1+bg_w, bg_y1+bg_h
        bg_outline = border_color[:3] + (220,)
        if hasattr(overlay_draw, "rounded_rectangle"):
            overlay_draw.rounded_rectangle([bg_x1,bg_y1,bg_x2,bg_y2], radius=6, fill=(0,0,0,190), outline=bg_outline, width=1)
        else:
            overlay_draw.rectangle([bg_x1,bg_y1,bg_x2,bg_y2], fill=(0,0,0,190), outline=bg_outline, width=1)
        overlay_draw.multiline_text((bg_x1+pad_x, bg_y1+pad_y), label, fill=(255,255,255,255), font=small_font, spacing=2)
        drawn += 1
        type_counts[tag] = type_counts.get(tag, 0) + 1
        if drawn >= 40: break

    if drawn > 0:
        new_img = Image.alpha_composite(new_img, overlay)
        legend_overlay = Image.new("RGBA", new_img.size, (0,0,0,0))
        legend_draw = ImageDraw.Draw(legend_overlay)
        legend_font = mod._load_font(max(12, new_img.width // 80))
        legend_items = [f"{t}:{c}" for t,c in sorted(type_counts.items())]
        legend_text = "  ".join(legend_items) + f"  TOTAL:{drawn}"
        tw, th = mod._bbox_measure_multiline(legend_draw, legend_text, legend_font)
        ly = new_img.height - th - 12
        legend_draw.rectangle([0, ly-6, tw+20, new_img.height], fill=(0,0,0,200))
        legend_draw.text((8, ly), legend_text, fill=(255,255,255,240), font=legend_font)
        new_img = Image.alpha_composite(new_img, legend_overlay)

    new_img.save("/tmp/phone_screen_NEW_SoM.png")
    print(f"  Saved /tmp/phone_screen_NEW_SoM.png ({drawn} elements, types: {type_counts})")

    # Side-by-side comparison
    print("Creating side-by-side comparison...")
    old_loaded = Image.open("/tmp/phone_screen_OLD.png").convert("RGBA")
    sw, sh = 540, int(H * 540 / W)
    old_s = old_loaded.resize((sw, sh), Image.LANCZOS)
    new_s = new_img.resize((sw, sh), Image.LANCZOS)
    gap, hdr = 20, 60
    comp = Image.new("RGBA", (sw*2+gap, sh+hdr), (30,30,30,255))
    cd = ImageDraw.Draw(comp)
    try: hf = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 28)
    except: hf = ImageFont.load_default()
    cd.text((sw//2-60, 15), "BEFORE", fill=(255,80,80), font=hf)
    cd.text((sw+gap+sw//2-40, 15), "AFTER", fill=(80,255,80), font=hf)
    comp.paste(old_s, (0, hdr))
    comp.paste(new_s, (sw+gap, hdr))
    comp.save("/tmp/annotation_comparison.png")
    print(f"  Saved /tmp/annotation_comparison.png ({comp.width}x{comp.height})")
    print("\nDone! Open /tmp/annotation_comparison.png to see the difference.")

