"""
Mobile MCP-based tools for element finding and clicking
"""
import json
import logging

logger = logging.getLogger(__name__)

# Global references (set by device_testing_agent)
_mcp_client = None
_streaming_manager = None

def set_mcp_client(client):
    """Set the global Mobile MCP client reference."""
    global _mcp_client
    _mcp_client = client

def set_streaming_manager(manager):
    """Set the global streaming manager reference."""
    global _streaming_manager
    _streaming_manager = manager

def get_mcp_client():
    if _mcp_client is None:
        raise RuntimeError("Mobile MCP client not initialized")
    return _mcp_client

def get_streaming_manager():
    if _streaming_manager is None:
        raise RuntimeError("Streaming manager not initialized")
    return _streaming_manager

async def create_mobile_session(device_id: str, app_package: str = None) -> str:
    """Create a Mobile MCP session for a device."""
    try:
        manager = get_streaming_manager()
        session_id = await manager.create_session(
            device_id=device_id,
            enable_streaming=False,
            fps=2
        )

        # Optionally launch app
        if app_package:
            client = get_mcp_client()
            await client.launch_app(device_id, app_package)

        return json.dumps({
            "success": True,
            "session_id": session_id,
            "device_id": device_id,
            "message": f"Created Mobile MCP session for {device_id}"
        })
    except Exception as e:
        logger.error(f"Error creating Mobile MCP session: {e}")
        return json.dumps({"error": str(e)})

async def find_elements_on_device(device_id: str, search_text: str = None, element_type: str = None) -> str:
    """Find elements on device using Mobile MCP accessibility tree."""
    try:
        client = get_mcp_client()
        elements = await client.list_elements_on_screen(device_id)

        # Log raw response for debugging
        logger.info(f"Mobile MCP list_elements_on_screen returned {len(elements)} elements for {device_id}")
        if len(elements) == 0:
            logger.warning(f"No elements found on {device_id}. Device may be on home screen or accessibility tree is empty. Try launching an app first.")

        # Filter by search_text if provided
        if search_text:
            search_lower = search_text.lower()
            elements = [
                e for e in elements
                if search_lower in e.get("text", "").lower() or search_lower in e.get("label", "").lower()
            ]

        # Filter by element_type if provided
        if element_type:
            type_lower = element_type.lower()
            elements = [e for e in elements if type_lower in e.get("type", "").lower()]

        # Provide helpful message if no elements found
        if len(elements) == 0:
            message = f"Found 0 elements on {device_id}. "
            if not search_text and not element_type:
                message += "The screen may be blank or on the home screen. Try launching an app (e.g., Settings, Chrome) first, then list elements again."
            else:
                message += f"No elements matched the search criteria (text='{search_text}', type='{element_type}')."
        else:
            message = f"Found {len(elements)} elements on {device_id}"

        return json.dumps({
            "success": True,
            "device_id": device_id,
            "elements": elements[:50],
            "total_found": len(elements),
            "message": message
        })
    except Exception as e:
        logger.error(f"Error finding elements: {e}")
        return json.dumps({"error": str(e)})

async def click_element_by_text(device_id: str, text: str) -> str:
    """Click an element by text using Mobile MCP."""
    try:
        client = get_mcp_client()

        # List elements and find matching one
        elements = await client.list_elements_on_screen(device_id)
        target = None
        for elem in elements:
            elem_text = elem.get("text", "")
            elem_label = elem.get("label", "")
            if text in elem_text or text in elem_label:
                target = elem
                break

        if not target:
            return json.dumps({"error": f"Element with text '{text}' not found"})

        # Calculate center coordinates
        coords = target.get("coordinates", {})
        x = coords.get("x", 0)
        y = coords.get("y", 0)
        width = coords.get("width", 0)
        height = coords.get("height", 0)
        cx = x + width // 2
        cy = y + height // 2

        # Click at center
        result = await client.click_on_screen(device_id, cx, cy)

        return json.dumps({
            "success": True,
            "device_id": device_id,
            "text": text,
            "tap": {"x": cx, "y": cy},
            "message": result
        })
    except Exception as e:
        logger.error(f"Error clicking element: {e}")
        return json.dumps({"error": str(e)})

