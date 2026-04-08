#!/usr/bin/env python3
"""Check what's on screen for both emulators."""
import subprocess
import os

def run_cmd(cmd):
    """Run a shell command and return output."""
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    return result.stdout + result.stderr

def main():
    print("=== EMULATOR 5556 (YouTube) ===")
    # Dump UI
    run_cmd("adb -s emulator-5556 shell uiautomator dump /sdcard/ui.xml")
    # Pull and parse
    run_cmd("adb -s emulator-5556 pull /sdcard/ui.xml /tmp/ui_5556.xml")
    
    if os.path.exists("/tmp/ui_5556.xml"):
        with open("/tmp/ui_5556.xml", "r") as f:
            content = f.read()
        # Extract text attributes
        import re
        texts = re.findall(r'text="([^"]+)"', content)
        texts = [t for t in texts if t.strip()]  # Filter empty
        print(f"Found {len(texts)} text elements:")
        for i, t in enumerate(texts[:30]):
            print(f"  {i+1}. {t}")
    else:
        print("Failed to get UI dump for 5556")
    
    print("\n=== EMULATOR 5560 (Chrome) ===")
    # Dump UI
    run_cmd("adb -s emulator-5560 shell uiautomator dump /sdcard/ui.xml")
    # Pull and parse
    run_cmd("adb -s emulator-5560 pull /sdcard/ui.xml /tmp/ui_5560.xml")
    
    if os.path.exists("/tmp/ui_5560.xml"):
        with open("/tmp/ui_5560.xml", "r") as f:
            content = f.read()
        # Extract text attributes
        import re
        texts = re.findall(r'text="([^"]+)"', content)
        texts = [t for t in texts if t.strip()]  # Filter empty
        print(f"Found {len(texts)} text elements:")
        for i, t in enumerate(texts[:40]):
            print(f"  {i+1}. {t}")
    else:
        print("Failed to get UI dump for 5560")

if __name__ == "__main__":
    main()

