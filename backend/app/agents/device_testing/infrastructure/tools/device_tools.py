"""
Device action tools for AI Agent - Mobile MCP implementation
"""
import json
import os
from datetime import datetime
import logging

logger = logging.getLogger(__name__)

# Global reference to Mobile MCP client (set by device_testing_agent)
_mcp_client = None

def set_mcp_client(client):
    """Set the global Mobile MCP client reference."""
    global _mcp_client
    _mcp_client = client


async def execute_device_action(
    device_id: str,
    action: str,
    params_json: str = "{}"
) -> str:
    """
    Execute a direct action on a device using Mobile MCP.

    Args:
        device_id: Device ID (e.g., 'emulator-5554')
        action: Action to execute (scroll, tap, screenshot, open_app, type_text, press_button, swipe)
        params_json: JSON string with action parameters

    Returns:
        JSON string with action result
    """
    if not _mcp_client:
        return json.dumps({"error": "Mobile MCP client not initialized"})

    try:
        params = json.loads(params_json) if params_json else {}

        if action == "scroll":
            direction = params.get("direction", "down")
            # Map scroll direction to swipe direction (scroll down = swipe up)
            swipe_dir = {"down": "up", "up": "down", "left": "right", "right": "left"}.get(direction, "up")
            result = await _mcp_client.swipe_on_screen(device_id, swipe_dir)
            return json.dumps({"success": True, "action": "scroll", "direction": direction, "result": result})

        elif action == "tap":
            x = params.get("x", 540)
            y = params.get("y", 960)
            result = await _mcp_client.click_on_screen(device_id, x, y)
            return json.dumps({"success": True, "action": "tap", "x": x, "y": y, "result": result})

        elif action == "screenshot":
            # Get screenshot and save to file
            # DO NOT return base64 data - it pollutes the context with massive strings
            screenshot_data = await _mcp_client.take_screenshot(device_id)

            # Save to file for reference
            screenshot_dir = os.path.join(os.getcwd(), "screenshots")
            os.makedirs(screenshot_dir, exist_ok=True)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"{device_id}_{timestamp}.png"
            filepath = os.path.join(screenshot_dir, filename)

            # Save the base64 data to file if available
            if screenshot_data and screenshot_data.get("type") == "image":
                import base64
                image_data = screenshot_data.get("data", "")
                if image_data:
                    with open(filepath, "wb") as f:
                        f.write(base64.b64decode(image_data))

                    # Return simple confirmation WITHOUT base64 data
                    # This prevents massive base64 strings from polluting the agent context
                    return f"Screenshot captured and saved to {filepath}. Use find_elements_on_device to see interactive elements."

            # Fallback if screenshot failed
            return f"Screenshot saved to {filepath}, but image data not available."

        elif action == "open_app":
            package = params.get("package", "com.android.settings")
            result = await _mcp_client.launch_app(device_id, package)
            return json.dumps({"success": True, "action": "open_app", "package": package, "result": result})

        elif action == "type_text":
            text = params.get("text", "")
            result = await _mcp_client.type_keys(device_id, text)
            return json.dumps({"success": True, "action": "type_text", "text": text, "result": result})

        elif action == "press_button":
            button = params.get("button", "back").upper()
            result = await _mcp_client.press_button(device_id, button)
            return json.dumps({"success": True, "action": "press_button", "button": button, "result": result})

        elif action == "swipe":
            direction = params.get("direction", "left")
            result = await _mcp_client.swipe_on_screen(device_id, direction)
            return json.dumps({"success": True, "action": "swipe", "direction": direction, "result": result})

        else:
            return json.dumps({"error": f"Unknown action: {action}"})

    except Exception as e:
        logger.error(f"Error executing device action: {e}")
        return json.dumps({"error": str(e), "action": action})

