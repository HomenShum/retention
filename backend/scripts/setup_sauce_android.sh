#!/usr/bin/env bash
# setup_sauce_android.sh — Download and install Sauce Labs Android demo APK
#
# Usage:
#   cd /path/to/my-fullstack-app
#   bash backend/scripts/setup_sauce_android.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
APK_DIR="${BACKEND_DIR}/data/apks"
APK_NAME="sauce_demo.apk"
APK_PATH="${APK_DIR}/${APK_NAME}"
DOWNLOAD_URL="https://github.com/saucelabs/my-demo-app-android/releases/download/1.0.23/mda-1.0.23-17.apk"
ORIGINAL_FILENAME="mda-1.0.23-17.apk"

echo "=== Sauce Labs Android Demo App Setup ==="
echo ""

# Step 1: Create APK directory
echo "[1/5] Creating APK directory: ${APK_DIR}"
mkdir -p "${APK_DIR}"
echo "      OK: Directory ready."

# Step 2: Check if APK already exists
echo "[2/5] Checking for existing APK at ${APK_PATH}"
if [ -f "${APK_PATH}" ]; then
    echo "      SKIP: sauce_demo.apk already exists ($(du -sh "${APK_PATH}" | cut -f1))."
else
    echo "      Not found. Downloading..."
    # Step 3: Download APK
    echo "[3/5] Downloading from:"
    echo "      ${DOWNLOAD_URL}"
    if command -v curl &>/dev/null; then
        curl -L --progress-bar -o "${APK_DIR}/${ORIGINAL_FILENAME}" "${DOWNLOAD_URL}"
    elif command -v wget &>/dev/null; then
        wget --show-progress -O "${APK_DIR}/${ORIGINAL_FILENAME}" "${DOWNLOAD_URL}"
    else
        echo "ERROR: Neither curl nor wget is available. Install one and retry."
        exit 1
    fi

    # Step 4: Rename to sauce_demo.apk
    echo "[4/5] Renaming ${ORIGINAL_FILENAME} -> ${APK_NAME}"
    mv "${APK_DIR}/${ORIGINAL_FILENAME}" "${APK_PATH}"
    echo "      OK: APK saved to ${APK_PATH} ($(du -sh "${APK_PATH}" | cut -f1))."
fi

# Step 5: Check for running emulator via adb
echo "[5/5] Checking for connected Android emulator..."
if ! command -v adb &>/dev/null; then
    echo "      WARNING: adb not found in PATH. Skipping install step."
    echo "               Install Android SDK platform-tools and ensure adb is on PATH."
    echo ""
    echo "=== Setup Status ==="
    echo "  APK:      ${APK_PATH}"
    echo "  Install:  SKIPPED (adb not available)"
    exit 0
fi

ADB_OUTPUT="$(adb devices 2>&1)"
echo "${ADB_OUTPUT}"

if echo "${ADB_OUTPUT}" | grep -q "emulator"; then
    EMULATOR_ID="$(echo "${ADB_OUTPUT}" | grep "emulator" | awk '{print $1}' | head -n1)"
    echo "      Found emulator: ${EMULATOR_ID}"
    echo ""
    echo "      Installing sauce_demo.apk on ${EMULATOR_ID}..."
    adb -s "${EMULATOR_ID}" install -r "${APK_PATH}"
    echo "      OK: APK installed successfully."
    echo ""
    echo "=== Setup Status ==="
    echo "  APK:      ${APK_PATH}"
    echo "  Emulator: ${EMULATOR_ID}"
    echo "  Install:  SUCCESS"
    echo "  Package:  com.saucelabs.mydemoapp.android"
    echo ""
    echo "To launch the app manually:"
    echo "  adb -s ${EMULATOR_ID} shell monkey -p com.saucelabs.mydemoapp.android -c android.intent.category.LAUNCHER 1"
else
    echo "      WARNING: No emulator detected in adb devices output."
    echo "               APK was downloaded but not installed."
    echo ""
    echo "      To start an emulator:"
    echo "        emulator -avd Pixel_8 -no-audio -no-window"
    echo "      Then re-run this script to install the APK."
    echo ""
    echo "=== Setup Status ==="
    echo "  APK:      ${APK_PATH}"
    echo "  Install:  SKIPPED (no emulator running)"
fi
