"""
Genymotion Cloud Provider

Provides cloud-based Android emulators via Genymotion SaaS API.
https://cloud.genymotion.com/

Required environment variables:
- GENYMOTION_API_TOKEN: API token from Genymotion Cloud dashboard
- GENYMOTION_API_URL: (optional) API base URL, defaults to https://api.cloud.genymotion.com

Pricing: ~$0.05/minute per device instance
"""

import os
import logging
import httpx
from typing import Dict, Any, List, Optional

from .base import CloudDeviceProvider, DeviceInfo, DeviceAction, DeviceStatus

logger = logging.getLogger(__name__)

# Default Genymotion Cloud API URL
GENYMOTION_API_URL = os.getenv("GENYMOTION_API_URL", "https://api.cloud.genymotion.com/v1")


class GenymotionCloudProvider(CloudDeviceProvider):
    """
    Genymotion Cloud device provider.
    
    Uses Genymotion SaaS API for cloud-based Android emulators.
    See: https://cloud.genymotion.com/docs
    """
    
    provider_name = "genymotion"
    
    def __init__(self, api_token: Optional[str] = None):
        self.api_token = api_token or os.getenv("GENYMOTION_API_TOKEN")
        self.api_url = GENYMOTION_API_URL
        self._client: Optional[httpx.AsyncClient] = None
        self._initialized = False
        self._instances: Dict[str, str] = {}  # device_id -> instance_uuid
    
    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create HTTP client."""
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.api_url,
                headers={
                    "Authorization": f"Bearer {self.api_token}",
                    "Content-Type": "application/json"
                },
                timeout=60.0
            )
        return self._client
    
    async def initialize(self) -> bool:
        """Initialize connection to Genymotion Cloud."""
        if not self.api_token:
            logger.warning("GENYMOTION_API_TOKEN not set - cloud devices unavailable")
            return False
        try:
            client = await self._get_client()
            # Test API connection by listing recipes (device templates)
            response = await client.get("/recipes")
            if response.status_code == 200:
                self._initialized = True
                logger.info("Genymotion Cloud provider initialized")
                return True
            else:
                logger.error(f"Genymotion API error: {response.status_code}")
                return False
        except Exception as e:
            logger.error(f"Failed to initialize Genymotion provider: {e}")
            return False
    
    async def list_available_templates(self) -> List[Dict[str, Any]]:
        """List available device templates (recipes)."""
        client = await self._get_client()
        response = await client.get("/recipes")
        if response.status_code == 200:
            return response.json().get("recipes", [])
        return []
    
    async def list_devices(self) -> List[DeviceInfo]:
        """List running cloud instances."""
        client = await self._get_client()
        response = await client.get("/instances")
        devices = []
        if response.status_code == 200:
            for instance in response.json().get("instances", []):
                status = DeviceStatus.ONLINE if instance.get("state") == "READY" else DeviceStatus.BOOTING
                devices.append(DeviceInfo(
                    device_id=instance.get("uuid", ""),
                    name=instance.get("name", ""),
                    platform="android",
                    status=status,
                    os_version=instance.get("android_version", "unknown"),
                    screen_width=instance.get("width", 1080),
                    screen_height=instance.get("height", 1920),
                    provider="genymotion",
                    metadata={"adb_serial": instance.get("adb_serial")}
                ))
        return devices
    
    async def start_device(self, device_template: str) -> DeviceInfo:
        """Start a new cloud device instance."""
        client = await self._get_client()
        response = await client.post("/instances", json={
            "recipe_uuid": device_template,
            "name": f"retention-{device_template[:8]}"
        })
        if response.status_code in (200, 201):
            instance = response.json()
            device_id = instance.get("uuid", "")
            self._instances[device_id] = device_id
            return DeviceInfo(
                device_id=device_id,
                name=instance.get("name", ""),
                platform="android",
                status=DeviceStatus.BOOTING,
                os_version=instance.get("android_version", "unknown"),
                provider="genymotion"
            )
        raise Exception(f"Failed to start device: {response.text}")
    
    async def stop_device(self, device_id: str) -> bool:
        """Stop and delete a cloud instance."""
        client = await self._get_client()
        response = await client.delete(f"/instances/{device_id}")
        if device_id in self._instances:
            del self._instances[device_id]
        return response.status_code in (200, 204)
    
    async def take_screenshot(self, device_id: str) -> Dict[str, Any]:
        """Take screenshot from cloud device."""
        client = await self._get_client()
        response = await client.get(f"/instances/{device_id}/screenshot")
        if response.status_code == 200:
            import base64
            return {
                "type": "image",
                "data": base64.b64encode(response.content).decode(),
                "mimeType": "image/png"
            }
        return {"error": f"Screenshot failed: {response.status_code}"}
    
    async def get_ui_elements(self, device_id: str) -> List[Dict[str, Any]]:
        """Get UI hierarchy from cloud device via uiautomator dump."""
        import re
        import xml.etree.ElementTree as ET

        client = await self._get_client()
        response = await client.post(f"/instances/{device_id}/shell", json={
            "command": "uiautomator dump /dev/tty"
        })
        if response.status_code != 200:
            return []

        xml_content = response.text.strip()
        xml_start = xml_content.find("<?xml")
        if xml_start < 0:
            xml_start = xml_content.find("<hierarchy")
        if xml_start < 0:
            return []
        xml_content = xml_content[xml_start:]

        try:
            root = ET.fromstring(xml_content)
        except ET.ParseError:
            return []

        elements = []
        bounds_re = re.compile(r'\[(\d+),(\d+)\]\[(\d+),(\d+)\]')
        for node in root.iter('node'):
            bounds = node.get('bounds', '')
            m = bounds_re.match(bounds)
            if not m:
                continue
            x1, y1, x2, y2 = map(int, m.groups())
            w, h = x2 - x1, y2 - y1
            if w <= 0 or h <= 0:
                continue
            text = node.get('text', '')
            desc = node.get('content-desc', '')
            rid = node.get('resource-id', '')
            cls = node.get('class', '')
            name = text or desc or (rid.split('/')[-1].replace('_', ' ') if rid else '') or cls.split('.')[-1]
            if not name:
                continue
            elements.append({
                "name": name, "text": text, "content_desc": desc,
                "resource_id": rid, "class": cls,
                "x": (x1 + x2) // 2, "y": (y1 + y2) // 2,
                "width": w, "height": h,
                "clickable": node.get('clickable', 'false') == 'true',
                "enabled": node.get('enabled', 'true') == 'true',
            })
        return elements

    async def click(self, device_id: str, x: int, y: int) -> DeviceAction:
        """Click at coordinates via ADB shell."""
        client = await self._get_client()
        response = await client.post(f"/instances/{device_id}/shell", json={
            "command": f"input tap {x} {y}"
        })
        return DeviceAction(
            success=response.status_code == 200,
            action="click", device_id=device_id,
            result=response.text if response.status_code == 200 else None,
            error=response.text if response.status_code != 200 else None
        )

    async def swipe(self, device_id: str, direction: str,
                    start_x: Optional[int] = None, start_y: Optional[int] = None,
                    distance: Optional[int] = None) -> DeviceAction:
        """Swipe on device via ADB shell."""
        # Default center coordinates
        sx, sy = start_x or 540, start_y or 960
        dist = distance or 500

        # Calculate end coordinates based on direction
        directions = {
            "up": (sx, sy, sx, sy - dist),
            "down": (sx, sy, sx, sy + dist),
            "left": (sx, sy, sx - dist, sy),
            "right": (sx, sy, sx + dist, sy),
        }
        x1, y1, x2, y2 = directions.get(direction.lower(), (sx, sy, sx, sy - dist))

        client = await self._get_client()
        response = await client.post(f"/instances/{device_id}/shell", json={
            "command": f"input swipe {x1} {y1} {x2} {y2} 300"
        })
        return DeviceAction(success=response.status_code == 200, action="swipe", device_id=device_id)

    async def type_text(self, device_id: str, text: str) -> DeviceAction:
        """Type text via ADB shell."""
        client = await self._get_client()
        # Escape special characters for shell
        escaped = text.replace("'", "'\\''")
        response = await client.post(f"/instances/{device_id}/shell", json={
            "command": f"input text '{escaped}'"
        })
        return DeviceAction(success=response.status_code == 200, action="type", device_id=device_id)

    async def press_button(self, device_id: str, button: str) -> DeviceAction:
        """Press hardware button via ADB shell."""
        # Map button names to Android keycodes
        keycodes = {
            "BACK": 4, "HOME": 3, "MENU": 82, "ENTER": 66,
            "VOLUME_UP": 24, "VOLUME_DOWN": 25, "POWER": 26
        }
        keycode = keycodes.get(button.upper(), 4)

        client = await self._get_client()
        response = await client.post(f"/instances/{device_id}/shell", json={
            "command": f"input keyevent {keycode}"
        })
        return DeviceAction(success=response.status_code == 200, action="button", device_id=device_id)

    async def install_app(self, device_id: str, app_path: str) -> DeviceAction:
        """Install APK on cloud device."""
        client = await self._get_client()
        # Upload APK first
        with open(app_path, 'rb') as f:
            response = await client.post(
                f"/instances/{device_id}/install",
                files={"file": f}
            )
        return DeviceAction(
            success=response.status_code == 200,
            action="install", device_id=device_id,
            error=response.text if response.status_code != 200 else None
        )

    async def launch_app(self, device_id: str, package_name: str) -> DeviceAction:
        """Launch app via ADB shell."""
        client = await self._get_client()
        response = await client.post(f"/instances/{device_id}/shell", json={
            "command": f"monkey -p {package_name} -c android.intent.category.LAUNCHER 1"
        })
        return DeviceAction(success=response.status_code == 200, action="launch", device_id=device_id)

    async def cleanup(self) -> None:
        """Stop all instances and close client."""
        for device_id in list(self._instances.keys()):
            await self.stop_device(device_id)
        if self._client:
            await self._client.aclose()

