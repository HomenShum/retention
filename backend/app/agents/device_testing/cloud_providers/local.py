"""
Local Device Provider

Uses local Android emulator via Mobile MCP and ADB.
This is the default provider for development environments.
"""

import logging
from typing import Dict, Any, List, Optional

from .base import CloudDeviceProvider, DeviceInfo, DeviceAction, DeviceStatus

logger = logging.getLogger(__name__)


class LocalDeviceProvider(CloudDeviceProvider):
    """Provider for local Android emulators via Mobile MCP."""
    
    provider_name = "local"
    
    def __init__(self):
        self._mcp_client = None
        self._initialized = False
    
    async def _get_mcp_client(self):
        """Lazy load MCP client to avoid circular imports."""
        if self._mcp_client is None:
            from ..mobile_mcp_client import MobileMCPClient
            self._mcp_client = MobileMCPClient()
            await self._mcp_client.start()
        return self._mcp_client
    
    async def initialize(self) -> bool:
        """Initialize the local provider."""
        try:
            await self._get_mcp_client()
            self._initialized = True
            return True
        except Exception as e:
            logger.error(f"Failed to initialize local provider: {e}")
            return False
    
    async def list_devices(self) -> List[DeviceInfo]:
        """List local emulators via ADB."""
        import subprocess
        devices = []
        try:
            result = subprocess.run(
                ['adb', 'devices', '-l'],
                capture_output=True, text=True, timeout=5
            )
            for line in result.stdout.split('\n')[1:]:
                if 'device' in line and ('emulator' in line or 'device' in line.split()[1] if len(line.split()) > 1 else False):
                    parts = line.split()
                    device_id = parts[0]
                    devices.append(DeviceInfo(
                        device_id=device_id,
                        name=device_id,
                        platform="android",
                        status=DeviceStatus.ONLINE,
                        os_version="unknown",
                        provider="local"
                    ))
        except Exception as e:
            logger.error(f"Error listing local devices: {e}")
        return devices
    
    async def start_device(self, device_template: str) -> DeviceInfo:
        """Start a local emulator."""
        from ..emulator_tools.emulator_management import launch_emulators
        import json
        result = json.loads(await launch_emulators(1))
        if "error" in result:
            raise Exception(result["error"])
        device_id = result["launched"][0]
        return DeviceInfo(
            device_id=device_id,
            name=device_template,
            platform="android",
            status=DeviceStatus.BOOTING,
            os_version="unknown",
            provider="local"
        )
    
    async def stop_device(self, device_id: str) -> bool:
        """Stop a local emulator."""
        import subprocess
        try:
            subprocess.run(['adb', '-s', device_id, 'emu', 'kill'], timeout=10)
            return True
        except (subprocess.SubprocessError, OSError):
            return False
    
    async def take_screenshot(self, device_id: str) -> Dict[str, Any]:
        """Take screenshot via Mobile MCP."""
        client = await self._get_mcp_client()
        return await client.take_screenshot(device_id)
    
    async def get_ui_elements(self, device_id: str) -> List[Dict[str, Any]]:
        """Get UI elements via Mobile MCP."""
        client = await self._get_mcp_client()
        return await client.list_elements_on_screen(device_id)
    
    async def click(self, device_id: str, x: int, y: int) -> DeviceAction:
        """Click via Mobile MCP."""
        client = await self._get_mcp_client()
        result = await client.click_on_screen(device_id, x, y)
        return DeviceAction(success=True, action="click", device_id=device_id, result=result)
    
    async def swipe(self, device_id: str, direction: str, 
                    start_x: Optional[int] = None, start_y: Optional[int] = None,
                    distance: Optional[int] = None) -> DeviceAction:
        """Swipe via Mobile MCP."""
        client = await self._get_mcp_client()
        result = await client.swipe_on_screen(device_id, direction, start_x, start_y, distance)
        return DeviceAction(success=True, action="swipe", device_id=device_id, result=result)
    
    async def type_text(self, device_id: str, text: str) -> DeviceAction:
        """Type text via Mobile MCP."""
        client = await self._get_mcp_client()
        result = await client.enter_text(device_id, text)
        return DeviceAction(success=True, action="type", device_id=device_id, result=result)
    
    async def press_button(self, device_id: str, button: str) -> DeviceAction:
        """Press button via Mobile MCP."""
        client = await self._get_mcp_client()
        result = await client.press_button(device_id, button)
        return DeviceAction(success=True, action="button", device_id=device_id, result=result)
    
    async def install_app(self, device_id: str, app_path: str) -> DeviceAction:
        """Install app via ADB."""
        import subprocess
        result = subprocess.run(['adb', '-s', device_id, 'install', app_path], capture_output=True, text=True)
        return DeviceAction(success=result.returncode == 0, action="install", device_id=device_id, result=result.stdout)
    
    async def launch_app(self, device_id: str, package_name: str) -> DeviceAction:
        """Launch app via Mobile MCP."""
        client = await self._get_mcp_client()
        result = await client.launch_app(device_id, package_name)
        return DeviceAction(success=True, action="launch", device_id=device_id, result=result)
    
    async def cleanup(self) -> None:
        """Cleanup MCP client."""
        if self._mcp_client:
            await self._mcp_client.stop()

