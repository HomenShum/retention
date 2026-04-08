#!/bin/bash
echo "=== EMULATOR 5556 (YouTube) ==="
adb -s emulator-5556 shell uiautomator dump /sdcard/ui.xml 2>/dev/null
adb -s emulator-5556 pull /sdcard/ui.xml /tmp/ui_5556.xml 2>/dev/null
grep -oE 'text="[^"]*"' /tmp/ui_5556.xml | head -30

echo ""
echo "=== EMULATOR 5560 (Chrome) ==="
adb -s emulator-5560 shell uiautomator dump /sdcard/ui.xml 2>/dev/null
adb -s emulator-5560 pull /sdcard/ui.xml /tmp/ui_5560.xml 2>/dev/null
grep -oE 'text="[^"]*"' /tmp/ui_5560.xml | head -40

