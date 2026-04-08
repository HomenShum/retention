"""
Base classes for cloud device providers.

This module defines the abstract interface that all cloud device providers must implement.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional
from enum import Enum


class DeviceStatus(str, Enum):
    """Device status enumeration."""
    AVAILABLE = "available"
    BOOTING = "booting"
    ONLINE = "online"
    OFFLINE = "offline"
    ERROR = "error"


@dataclass
class DeviceInfo:
    """Information about a cloud device."""
    device_id: str
    name: str
    platform: str  # "android" or "ios"
    status: DeviceStatus
    os_version: str
    screen_width: int = 1080
    screen_height: int = 2400
    provider: str = "local"
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class DeviceAction:
    """Result of a device action."""
    success: bool
    action: str
    device_id: str
    result: Any = None
    error: Optional[str] = None
    screenshot_base64: Optional[str] = None


class CloudDeviceProvider(ABC):
    """Abstract base class for cloud device providers."""
    
    provider_name: str = "base"
    
    @abstractmethod
    async def initialize(self) -> bool:
        """Initialize the provider connection."""
        pass
    
    @abstractmethod
    async def list_devices(self) -> List[DeviceInfo]:
        """List all available devices."""
        pass
    
    @abstractmethod
    async def start_device(self, device_template: str) -> DeviceInfo:
        """Start a new device instance from a template/image."""
        pass
    
    @abstractmethod
    async def stop_device(self, device_id: str) -> bool:
        """Stop and release a device."""
        pass
    
    @abstractmethod
    async def take_screenshot(self, device_id: str) -> Dict[str, Any]:
        """
        Take a screenshot of the device.
        
        Returns:
            Dict with keys: type, data (base64), mimeType
        """
        pass
    
    @abstractmethod
    async def get_ui_elements(self, device_id: str) -> List[Dict[str, Any]]:
        """Get UI elements from the device screen."""
        pass
    
    @abstractmethod
    async def click(self, device_id: str, x: int, y: int) -> DeviceAction:
        """Click at coordinates on the device."""
        pass
    
    @abstractmethod
    async def swipe(
        self, 
        device_id: str, 
        direction: str,
        start_x: Optional[int] = None,
        start_y: Optional[int] = None,
        distance: Optional[int] = None
    ) -> DeviceAction:
        """Swipe on the device screen."""
        pass
    
    @abstractmethod
    async def type_text(self, device_id: str, text: str) -> DeviceAction:
        """Type text on the device."""
        pass
    
    @abstractmethod
    async def press_button(self, device_id: str, button: str) -> DeviceAction:
        """Press a hardware button (BACK, HOME, etc.)."""
        pass
    
    @abstractmethod
    async def install_app(self, device_id: str, app_path: str) -> DeviceAction:
        """Install an app on the device."""
        pass
    
    @abstractmethod
    async def launch_app(self, device_id: str, package_name: str) -> DeviceAction:
        """Launch an app on the device."""
        pass
    
    async def cleanup(self) -> None:
        """Cleanup provider resources."""
        pass

