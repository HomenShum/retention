"""
Device Provider Factory

Automatically selects the appropriate device provider based on environment.
Priority order:
1. If GENYMOTION_API_TOKEN is set -> Genymotion Cloud
2. If AWS_DEVICE_FARM_ARN is set -> AWS Device Farm (future)
3. If BROWSERSTACK_KEY is set -> BrowserStack (future)
4. Default -> Local emulator

Usage:
    from cloud_providers.factory import get_device_provider
    
    provider = await get_device_provider()
    devices = await provider.list_devices()
"""

import os
import logging
from typing import Optional

from .base import CloudDeviceProvider
from .local import LocalDeviceProvider
from .genymotion import GenymotionCloudProvider

logger = logging.getLogger(__name__)

# Singleton instance
_provider_instance: Optional[CloudDeviceProvider] = None


async def get_device_provider(force_provider: Optional[str] = None) -> CloudDeviceProvider:
    """
    Get the appropriate device provider based on environment.
    
    Args:
        force_provider: Force a specific provider ("local", "genymotion", etc.)
    
    Returns:
        Initialized CloudDeviceProvider instance
    """
    global _provider_instance
    
    if _provider_instance is not None and force_provider is None:
        return _provider_instance
    
    provider_type = force_provider or os.getenv("DEVICE_PROVIDER", "auto")
    
    if provider_type == "auto":
        # Auto-detect based on available credentials
        if os.getenv("GENYMOTION_API_TOKEN"):
            provider_type = "genymotion"
        elif os.getenv("AWS_DEVICE_FARM_ARN"):
            provider_type = "aws"  # Future
        elif os.getenv("BROWSERSTACK_KEY"):
            provider_type = "browserstack"  # Future
        else:
            provider_type = "local"
    
    logger.info(f"Using device provider: {provider_type}")
    
    if provider_type == "genymotion":
        provider = GenymotionCloudProvider()
    elif provider_type == "local":
        provider = LocalDeviceProvider()
    else:
        logger.warning(f"Unknown provider '{provider_type}', falling back to local")
        provider = LocalDeviceProvider()
    
    # Initialize the provider
    success = await provider.initialize()
    if not success and provider_type != "local":
        logger.warning(f"Failed to initialize {provider_type}, falling back to local")
        provider = LocalDeviceProvider()
        await provider.initialize()
    
    _provider_instance = provider
    return provider


def get_provider_info() -> dict:
    """Get information about available providers and current selection."""
    return {
        "current": os.getenv("DEVICE_PROVIDER", "auto"),
        "available": {
            "local": {
                "name": "Local Emulator",
                "description": "Uses local Android emulator via ADB",
                "configured": True,  # Always available locally
                "requires": ["Android SDK", "ADB"]
            },
            "genymotion": {
                "name": "Genymotion Cloud",
                "description": "Cloud-based Android emulators",
                "configured": bool(os.getenv("GENYMOTION_API_TOKEN")),
                "requires": ["GENYMOTION_API_TOKEN"],
                "pricing": "~$0.05/min per device"
            },
            "aws": {
                "name": "AWS Device Farm",
                "description": "Real devices in AWS cloud",
                "configured": bool(os.getenv("AWS_DEVICE_FARM_ARN")),
                "requires": ["AWS_DEVICE_FARM_ARN", "AWS credentials"],
                "status": "coming_soon"
            },
            "browserstack": {
                "name": "BrowserStack",
                "description": "Real device cloud",
                "configured": bool(os.getenv("BROWSERSTACK_KEY")),
                "requires": ["BROWSERSTACK_KEY", "BROWSERSTACK_USER"],
                "status": "coming_soon"
            }
        },
        "environment": {
            "DEVICE_PROVIDER": os.getenv("DEVICE_PROVIDER", "auto"),
            "GENYMOTION_API_TOKEN": "***" if os.getenv("GENYMOTION_API_TOKEN") else None,
            "AWS_DEVICE_FARM_ARN": os.getenv("AWS_DEVICE_FARM_ARN"),
            "BROWSERSTACK_KEY": "***" if os.getenv("BROWSERSTACK_KEY") else None,
        }
    }

