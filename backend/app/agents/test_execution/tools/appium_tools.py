import json
import logging

logger = logging.getLogger(__name__)

_appium_mcp_manager = None

def set_appium_mcp_manager(manager):
    global _appium_mcp_manager
    _appium_mcp_manager = manager

def get_appium_mcp_manager():
    if _appium_mcp_manager is None:
        raise RuntimeError("Appium MCP Manager not initialized")
    return _appium_mcp_manager

async def create_appium_session(device_id: str, app_package: str = None) -> str:
    try:
        manager = get_appium_mcp_manager()
        session_id = await manager.create_session(
            device_id=device_id,
            enable_streaming=False,
            fps=2
        )

        return json.dumps({
            "success": True,
            "session_id": session_id,
            "device_id": device_id,
            "message": f"Created Appium session for {device_id}"
        })
    except Exception as e:
        logger.error(f"Error creating Appium session: {e}")
        return json.dumps({"error": str(e)})

async def find_elements_on_device(device_id: str, search_text: str = None, element_type: str = None) -> str:
    try:
        manager = get_appium_mcp_manager()
        sessions = await manager.list_sessions()
        session = next((s for s in sessions if s.get("device_id") == device_id), None)

        if not session:
            return json.dumps({"error": f"No active session for device {device_id}"})

        session_id = session["session_id"]
        session_obj = manager._sessions.get(session_id)

        if not session_obj or not session_obj.client:
            return json.dumps({"error": "Session client not available"})

        page_source = await session_obj.client.get_source()

        import xml.etree.ElementTree as ET
        root = ET.fromstring(page_source)

        elements = []
        for elem in root.iter():
            text = elem.get('text', '')
            content_desc = elem.get('content-desc', '')
            resource_id = elem.get('resource-id', '')
            class_name = elem.get('class', '')
            clickable = elem.get('clickable', 'false') == 'true'

            if search_text:
                search_lower = search_text.lower()
                if not (search_lower in text.lower() or search_lower in content_desc.lower() or search_lower in resource_id.lower()):
                    continue

            if element_type:
                type_lower = element_type.lower()
                if type_lower not in class_name.lower():
                    continue

            if text or content_desc or resource_id:
                elements.append({
                    "text": text,
                    "content_desc": content_desc,
                    "resource_id": resource_id,
                    "class": class_name,
                    "clickable": clickable,
                    "bounds": elem.get('bounds', '')
                })

        return json.dumps({
            "success": True,
            "device_id": device_id,
            "elements": elements[:50],
            "total_found": len(elements),
            "message": f"Found {len(elements)} elements on {device_id}"
        })
    except Exception as e:
        logger.error(f"Error finding elements: {e}")
        return json.dumps({"error": str(e)})

async def click_element_by_text(device_id: str, text: str) -> str:
    try:
        manager = get_appium_mcp_manager()
        result = await manager.execute_action(
            session_id=device_id,
            action_type="click",
            params={"text": text}
        )

        return json.dumps({
            "success": result.get("success", False),
            "device_id": device_id,
            "text": text,
            "message": result.get("message", f"Clicked element with text '{text}' on {device_id}")
        })
    except Exception as e:
        logger.error(f"Error clicking element: {e}")
        return json.dumps({"error": str(e)})

