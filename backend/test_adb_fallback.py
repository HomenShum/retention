#!/usr/bin/env python3
"""Test parallel YouTube + Chrome navigation flow."""
import asyncio
import subprocess
import sys
import time

DEVICE_1 = "emulator-5556"
DEVICE_2 = "emulator-5560"
YOUTUBE_PKG = "com.google.android.youtube"
CHROME_PKG = "com.android.chrome"
SEARCH_QUERY = "langchain deep agent"

async def run_adb(device: str, *args, timeout: float = 30.0) -> tuple[int, str, str]:
    """Run an ADB command and return (returncode, stdout, stderr)."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "adb", "-s", device, *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return proc.returncode, stdout.decode(), stderr.decode()
    except asyncio.TimeoutError:
        print(f"  [{device}] ADB command timed out: {args}")
        return -1, "", "timeout"

async def get_ui_elements(device: str) -> list[str]:
    """Get text elements and content descriptions from UI hierarchy."""
    await run_adb(device, "shell", "uiautomator", "dump", "/sdcard/ui.xml")
    _, stdout, _ = await run_adb(device, "shell", "cat", "/sdcard/ui.xml")
    import re
    # Get both text and content-desc attributes
    texts = re.findall(r'text="([^"]+)"', stdout)
    content_descs = re.findall(r'content-desc="([^"]+)"', stdout)
    all_elements = [t for t in texts if t.strip()] + [c for c in content_descs if c.strip()]
    return all_elements

async def launch_app(device: str, package: str) -> bool:
    """Launch an app on a device using am start with shell command."""
    print(f"  [{device}] Launching {package}...")

    # Use am start with known activities for reliability
    # Note: Use shell command to properly handle $ in activity names
    activity_map = {
        "com.google.android.youtube": "com.google.android.youtube/.app.honeycomb.Shell\\$HomeActivity",
        "com.android.chrome": "com.android.chrome/com.google.android.apps.chrome.Main",
    }

    if package in activity_map:
        component = activity_map[package]
        # Use shell command string to properly handle $ escaping
        cmd = f"adb -s {device} shell 'am start -n {component}'"
        proc = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=15.0)
        stdout_str = stdout.decode() if stdout else ""
        success = "Starting:" in stdout_str or "Activity" in stdout_str or "brought to the front" in stdout_str
    else:
        # Fallback to monkey
        rc, stdout, stderr = await run_adb(device, "shell", "monkey", "-p", package,
                                            "-c", "android.intent.category.LAUNCHER", "-v", "1")
        success = rc == 0 or "Events injected: 1" in stdout

    print(f"  [{device}] Launch {'SUCCESS' if success else 'FAILED'}")
    return success

async def click(device: str, x: int, y: int) -> bool:
    """Click at coordinates."""
    print(f"  [{device}] Clicking at ({x}, {y})...")
    rc, _, _ = await run_adb(device, "shell", "input", "tap", str(x), str(y))
    return rc == 0

async def type_text(device: str, text: str, submit: bool = False) -> bool:
    """Type text on device."""
    escaped = text.replace(" ", "%s")
    print(f"  [{device}] Typing '{text}'...")
    rc, _, _ = await run_adb(device, "shell", "input", "text", escaped)
    if rc != 0:
        return False
    if submit:
        print(f"  [{device}] Pressing Enter...")
        await run_adb(device, "shell", "input", "keyevent", "66")
    return True

async def press_home(device: str) -> bool:
    """Press home button."""
    rc, _, _ = await run_adb(device, "shell", "input", "keyevent", "KEYCODE_HOME")
    return rc == 0

async def main():
    print("=" * 60)
    print("Parallel YouTube + Chrome Navigation Test")
    print("=" * 60)

    # Step 1: Reset both devices to home
    print("\n[STEP 1] Resetting devices to home screen...")
    await asyncio.gather(press_home(DEVICE_1), press_home(DEVICE_2))
    await asyncio.sleep(1)

    # Step 2: Launch apps SEQUENTIALLY to avoid race conditions
    print("\n[STEP 2] Launching apps sequentially...")
    result1 = await launch_app(DEVICE_1, YOUTUBE_PKG)
    await asyncio.sleep(2)  # Wait for YouTube to fully load
    result2 = await launch_app(DEVICE_2, CHROME_PKG)
    await asyncio.sleep(2)  # Wait for Chrome to fully load

    if not (result1 and result2):
        print("ERROR: Failed to launch apps")
        return 1

    # Step 3: Check current UI state
    print("\n[STEP 3] Checking UI state...")
    elements_1, elements_2 = await asyncio.gather(
        get_ui_elements(DEVICE_1),
        get_ui_elements(DEVICE_2)
    )
    print(f"  [{DEVICE_1}] Elements: {elements_1[:5]}...")
    print(f"  [{DEVICE_2}] Elements: {elements_2[:5]}...")

    # Step 4: Handle Chrome welcome screens (there are multiple!)
    # First: "Use without an account" on sign-in screen
    if "Use without an account" in elements_2:
        print("\n[STEP 4a] Dismissing Chrome sign-in screen...")
        await click(DEVICE_2, 540, 2088)  # "Use without an account" button
        await asyncio.sleep(3)

        # Check for notification dialog
        elements_2 = await get_ui_elements(DEVICE_2)
        if "No thanks" in elements_2:
            print("\n[STEP 4b] Dismissing Chrome notification dialog...")
            await click(DEVICE_2, 565, 1721)  # "No thanks" button
            await asyncio.sleep(2)

    # Step 5: Search on YouTube - click search box and type
    print("\n[STEP 5] Searching on YouTube...")
    # Click on the search box (bounds: [154,154][573,242], center: ~363, 198)
    await click(DEVICE_1, 363, 198)
    await asyncio.sleep(1)
    # Clear any existing text and type new search
    await run_adb(DEVICE_1, "shell", "input", "keyevent", "KEYCODE_MOVE_END")
    await run_adb(DEVICE_1, "shell", "input", "keyevent", "--longpress", "KEYCODE_DEL")
    await asyncio.sleep(0.5)
    await type_text(DEVICE_1, SEARCH_QUERY, submit=True)
    await asyncio.sleep(3)

    # Step 6: Search on Chrome - use am start with URL (more reliable)
    print("\n[STEP 6] Searching on Chrome via URL...")
    search_url = f"https://www.google.com/search?q={SEARCH_QUERY.replace(' ', '+')}"
    await run_adb(DEVICE_2, "shell", "am", "start", "-a", "android.intent.action.VIEW", "-d", search_url)
    await asyncio.sleep(5)  # Wait for page to load

    # Step 8: Verify final state
    print("\n[STEP 8] Verifying final state...")
    elements_1, elements_2 = await asyncio.gather(
        get_ui_elements(DEVICE_1),
        get_ui_elements(DEVICE_2)
    )

    print(f"\n  [{DEVICE_1}] Final elements: {elements_1[:10]}")
    print(f"\n  [{DEVICE_2}] Final elements: {elements_2[:10]}")

    # Check if search was successful
    youtube_success = any("langchain" in e.lower() for e in elements_1)
    chrome_success = any("langchain" in e.lower() for e in elements_2)

    print("\n" + "=" * 60)
    print("Results:")
    print("=" * 60)
    print(f"  YouTube search: {'SUCCESS' if youtube_success else 'FAILED'}")
    print(f"  Chrome search: {'SUCCESS' if chrome_success else 'FAILED'}")

    return 0 if (youtube_success and chrome_success) else 1

if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

