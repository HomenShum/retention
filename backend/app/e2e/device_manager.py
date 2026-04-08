"""
Device Manager for E2E Tests

Handles device lifecycle:
- Emulator launch/shutdown
- Device connectivity verification
- App state reset between tests
"""

import asyncio
import json
import logging
import subprocess
from dataclasses import dataclass
from typing import List, Optional

from .config import DeviceConfig

logger = logging.getLogger(__name__)


@dataclass
class DeviceStatus:
    """Device status information"""
    device_id: str
    connected: bool
    platform: str = "android"
    ready: bool = False
    error: Optional[str] = None


class DeviceManager:
    """Manages device lifecycle for E2E tests"""
    
    def __init__(self, config: DeviceConfig = None):
        self.config = config or DeviceConfig()
        self._launched_emulators: List[str] = []
    
    async def setup(self) -> DeviceStatus:
        """Setup device for testing"""
        logger.info(f"[DeviceManager] Setting up device: {self.config.device_id}")
        
        # Check if device already connected
        status = await self.check_device()
        if status.connected:
            logger.info(f"[DeviceManager] Device already connected: {status.device_id}")
            return status
        
        # Auto-launch if configured
        if self.config.auto_launch:
            logger.info("[DeviceManager] Auto-launching emulator...")
            return await self.launch_emulator()
        
        return DeviceStatus(
            device_id=self.config.device_id,
            connected=False,
            error="Device not connected and auto_launch disabled"
        )
    
    async def check_device(self) -> DeviceStatus:
        """Check if device is connected"""
        try:
            result = subprocess.run(
                ["adb", "devices", "-l"],
                capture_output=True,
                text=True,
                timeout=10
            )
            
            for line in result.stdout.split("\n"):
                if self.config.device_id in line and "device" in line:
                    return DeviceStatus(
                        device_id=self.config.device_id,
                        connected=True,
                        ready=True
                    )
            
            return DeviceStatus(
                device_id=self.config.device_id,
                connected=False
            )
        except Exception as e:
            return DeviceStatus(
                device_id=self.config.device_id,
                connected=False,
                error=str(e)
            )
    
    async def launch_emulator(self) -> DeviceStatus:
        """Launch an Android emulator"""
        from app.agents.device_testing.emulator_tools.emulator_management import (
            launch_emulators,
            get_available_emulators
        )
        
        # Check existing emulators
        existing = json.loads(await get_available_emulators())
        if existing.get("emulators"):
            device_id = existing["emulators"][0]
            logger.info(f"[DeviceManager] Using existing emulator: {device_id}")
            self.config.device_id = device_id
            return DeviceStatus(device_id=device_id, connected=True, ready=True)
        
        # Launch new emulator
        result = json.loads(await launch_emulators(count=1))
        
        if "error" in result:
            return DeviceStatus(
                device_id=self.config.device_id,
                connected=False,
                error=result["error"]
            )
        
        if result.get("emulators"):
            device_id = result["emulators"][0]
            self._launched_emulators.append(device_id)
            
            # Wait for emulator to be ready
            logger.info(f"[DeviceManager] Waiting for emulator: {device_id}")
            await self._wait_for_device(device_id, timeout=90)
            
            self.config.device_id = device_id
            return DeviceStatus(device_id=device_id, connected=True, ready=True)
        
        return DeviceStatus(
            device_id=self.config.device_id,
            connected=False,
            error="Failed to launch emulator"
        )
    
    async def _wait_for_device(self, device_id: str, timeout: int = 60):
        """Wait for device to be ready"""
        import time
        start = time.time()
        while time.time() - start < timeout:
            result = subprocess.run(
                ["adb", "-s", device_id, "shell", "getprop", "sys.boot_completed"],
                capture_output=True, text=True, timeout=5
            )
            if result.stdout.strip() == "1":
                logger.info(f"[DeviceManager] Device ready: {device_id}")
                return
            await asyncio.sleep(2)
        logger.warning(f"[DeviceManager] Timeout waiting for device: {device_id}")
    
    async def reset_app_state(self, package: str) -> bool:
        """Clear app data to reset state"""
        try:
            result = subprocess.run(
                ["adb", "-s", self.config.device_id, "shell", "pm", "clear", package],
                capture_output=True, text=True, timeout=10
            )
            success = "Success" in result.stdout
            logger.info(f"[DeviceManager] Reset app {package}: {success}")
            return success
        except Exception as e:
            logger.error(f"[DeviceManager] Failed to reset app: {e}")
            return False
    
    async def teardown(self):
        """Cleanup launched emulators"""
        # Note: We don't kill emulators by default to speed up subsequent runs
        logger.info(f"[DeviceManager] Teardown complete (keeping emulators running)")

