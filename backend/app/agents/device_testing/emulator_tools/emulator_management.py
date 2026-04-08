"""
Emulator management tools for AI Agent
"""
import json
import os
import subprocess
from typing import Optional
import logging

logger = logging.getLogger(__name__)


async def launch_emulators(count: int = 1) -> str:
    """
    Launch Android emulators

    Args:
        count: Number of emulators to launch (1-20)

    Returns:
        JSON string with launched emulator IDs
    """
    avd_name = None
    try:
        # Validate count
        if count < 1 or count > 20:
            return json.dumps({"error": "Count must be between 1 and 20"})

        android_home = os.path.expanduser("~/Library/Android/sdk")
        emulator_path = os.path.join(android_home, "emulator", "emulator")

        # Check if emulator exists
        if not os.path.exists(emulator_path):
            return json.dumps({"error": f"Emulator not found at {emulator_path}"})

        # Get available AVDs
        result = subprocess.run(
            [emulator_path, '-list-avds'],
            capture_output=True,
            text=True,
            timeout=5
        )
        avds = [line.strip() for line in result.stdout.split('\n') if line.strip()]
        if not avds:
            return json.dumps({"error": "No AVDs available"})

        # Prefer stable AVDs, avoid problematic ones like Foldable
        preferred_avds = ["Pixel_6_API_36", "Pixel_5_API_36", "Pixel_8_API_36", "Pixel_7_API_36", "Medium_Phone_API_36.1"]
        stable_avds = []
        for preferred in preferred_avds:
            if preferred in avds:
                stable_avds.append(preferred)

        # If no preferred AVDs found, use non-foldable AVDs
        if not stable_avds:
            for avd in avds:
                if "foldable" not in avd.lower() and "tablet" not in avd.lower():
                    stable_avds.append(avd)

        # Last resort: use all AVDs
        if not stable_avds:
            stable_avds = avds

        logger.info(f"Available stable AVDs: {stable_avds}")

        # Get current emulator count
        adb_result = subprocess.run(
            ['adb', 'devices'],
            capture_output=True,
            text=True,
            timeout=5
        )
        current_count = len([line for line in adb_result.stdout.split('\n') if 'emulator' in line])
        logger.info(f"Currently running emulators: {current_count}")

        # Launch emulators - use different AVDs to avoid conflicts
        launched = []
        errors = []
        import time
        for i in range(min(count, 20)):
            port = 5554 + (current_count + i) * 2

            # Use different AVDs for each emulator to avoid conflicts
            # Cycle through stable AVDs
            selected_avd = stable_avds[i % len(stable_avds)] if not avd_name else avd_name
            logger.info(f"Launching emulator on port {port} with AVD {selected_avd}")

            try:
                cmd = [emulator_path, '-avd', selected_avd, '-port', str(port),
                       '-no-audio', '-no-window', '-gpu', 'swiftshader_indirect']

                process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    start_new_session=True
                )
                device_id = f"emulator-{port}"
                launched.append(device_id)
                logger.info(f"Launched emulator: {device_id} (PID: {process.pid}) with AVD {selected_avd}")

                # Add delay between launches to avoid conflicts
                if i < count - 1:
                    time.sleep(3)
            except Exception as e:
                error_msg = f"Failed to launch emulator on port {port}: {e}"
                logger.error(error_msg)
                errors.append(error_msg)

        result = {
            "status": "launched",
            "emulators": launched,
            "count": len(launched),
            "message": f"Launched {len(launched)} emulator(s). They will be ready in 30-60 seconds."
        }
        if errors:
            result["errors"] = errors
        return json.dumps(result)
    except Exception as e:
        logger.error(f"Error launching emulators: {e}")
        return json.dumps({"error": str(e)})


async def get_available_emulators() -> str:
    """
    Get list of currently running emulators
    
    Returns:
        JSON string with list of emulator IDs
    """
    try:
        result = subprocess.run(
            ['adb', 'devices', '-l'],
            capture_output=True,
            text=True,
            timeout=5
        )
        
        emulators = []
        for line in result.stdout.split('\n')[1:]:
            if 'device' in line and 'emulator' in line:
                device_id = line.split()[0]
                emulators.append(device_id)
        
        return json.dumps({
            "emulators": emulators,
            "count": len(emulators)
        })
    except Exception as e:
        logger.error(f"Error getting emulators: {e}")
        return json.dumps({"error": str(e)})

