"""
Cloud Device Providers

Abstraction layer for cloud-based Android emulators and device farms.
Supports multiple providers:
- Genymotion Cloud (SaaS)
- AWS Device Farm
- BrowserStack
- Local (default - local emulator)
"""

from .base import CloudDeviceProvider, DeviceInfo, DeviceAction, DeviceStatus
from .genymotion import GenymotionCloudProvider
from .local import LocalDeviceProvider
from .factory import get_device_provider, get_provider_info

__all__ = [
    "CloudDeviceProvider",
    "DeviceInfo",
    "DeviceAction",
    "DeviceStatus",
    "GenymotionCloudProvider",
    "LocalDeviceProvider",
    "get_device_provider",
    "get_provider_info",
]

