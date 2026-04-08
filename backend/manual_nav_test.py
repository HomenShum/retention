#!/usr/bin/env python3
"""Manual navigation test to verify the flow works."""
import subprocess
import time
import re
import os

def run_adb(device, *args):
    """Run an ADB command and return output."""
    cmd = ["adb", "-s", device] + list(args)
    result = subprocess.run(cmd, capture_output=True, text=True)
    return result.stdout + result.stderr

def get_ui_texts(device):
    """Get all text elements from the UI."""
    run_adb(device, "shell", "uiautomator", "dump", "/sdcard/ui.xml")
    run_adb(device, "pull", "/sdcard/ui.xml", f"/tmp/ui_{device}.xml")
    
    if os.path.exists(f"/tmp/ui_{device}.xml"):
        with open(f"/tmp/ui_{device}.xml", "r") as f:
            content = f.read()
        texts = re.findall(r'text="([^"]+)"', content)
        return [t for t in texts if t.strip()]
    return []

def find_element_bounds(device, text):
    """Find the bounds of an element by text."""
    if os.path.exists(f"/tmp/ui_{device}.xml"):
        with open(f"/tmp/ui_{device}.xml", "r") as f:
            content = f.read()
        # Find the element with this text and get its bounds
        pattern = rf'text="{re.escape(text)}"[^>]*bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"'
        match = re.search(pattern, content)
        if match:
            x1, y1, x2, y2 = map(int, match.groups())
            return ((x1 + x2) // 2, (y1 + y2) // 2)
    return None

def click(device, x, y):
    """Click at coordinates."""
    run_adb(device, "shell", "input", "tap", str(x), str(y))
    time.sleep(0.5)

def type_text(device, text, submit=False):
    """Type text."""
    # Escape special characters for shell
    escaped = text.replace(" ", "%s")
    run_adb(device, "shell", "input", "text", escaped)
    if submit:
        time.sleep(0.3)
        run_adb(device, "shell", "input", "keyevent", "66")  # ENTER
    time.sleep(0.5)

def main():
    print("=== STEP 1: Check current state ===")
    print("YouTube (5556):", get_ui_texts("emulator-5556")[:5])
    print("Chrome (5560):", get_ui_texts("emulator-5560")[:5])
    
    print("\n=== STEP 2: Handle Chrome welcome screen ===")
    # Click "Use without an account"
    coords = find_element_bounds("emulator-5560", "Use without an account")
    if coords:
        print(f"Clicking 'Use without an account' at {coords}")
        click("emulator-5560", coords[0], coords[1])
        time.sleep(1)
    else:
        print("Could not find 'Use without an account' button")
    
    print("\n=== STEP 3: Check Chrome state after dismissing welcome ===")
    texts = get_ui_texts("emulator-5560")
    print("Chrome texts:", texts[:10])
    
    print("\n=== STEP 4: Click YouTube search icon ===")
    # YouTube search icon is typically in top-right area
    # Let's find it by looking for the search content description
    run_adb("emulator-5556", "shell", "uiautomator", "dump", "/sdcard/ui.xml")
    run_adb("emulator-5556", "pull", "/sdcard/ui.xml", "/tmp/ui_emulator-5556.xml")
    with open("/tmp/ui_emulator-5556.xml", "r") as f:
        content = f.read()
    # Look for search icon
    search_match = re.search(r'content-desc="Search"[^>]*bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"', content)
    if search_match:
        x1, y1, x2, y2 = map(int, search_match.groups())
        x, y = (x1 + x2) // 2, (y1 + y2) // 2
        print(f"Clicking YouTube search at ({x}, {y})")
        click("emulator-5556", x, y)
        time.sleep(1)
    else:
        print("Could not find YouTube search icon, trying coordinates (980, 140)")
        click("emulator-5556", 980, 140)
        time.sleep(1)
    
    print("\n=== STEP 5: Type search query on both devices ===")
    # Type on YouTube
    print("Typing on YouTube...")
    type_text("emulator-5556", "langchain deep agent", submit=True)
    time.sleep(2)
    
    # Type on Chrome (in address bar)
    print("Clicking Chrome address bar...")
    click("emulator-5560", 540, 150)  # Address bar area
    time.sleep(0.5)
    print("Typing on Chrome...")
    type_text("emulator-5560", "langchain deep agent", submit=True)
    time.sleep(2)
    
    print("\n=== STEP 6: Check final state ===")
    print("YouTube (5556):", get_ui_texts("emulator-5556")[:15])
    print("Chrome (5560):", get_ui_texts("emulator-5560")[:15])

if __name__ == "__main__":
    main()

