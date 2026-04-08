"""
Appium Infrastructure Module

Provides core Appium infrastructure and tools for Android device automation:
- MCP Appium Client (WebDriver integration)
- Appium MCP Streaming Manager (session management, real-time streaming)
- Device tools (actions, element finding, session creation)
- Simulation tools (test execution infrastructure)
"""

from .tools.appium_tools import (
    create_appium_session,
    find_elements_on_device,
    click_element_by_text,
    set_appium_mcp_manager,
    get_appium_mcp_manager,
)
from .tools.device_tools import execute_device_action
from .tools.simulation_tools import create_simulation_tools
from .mcp_appium_client import MCPAppiumClient, Platform
from .appium_mcp_streaming import AppiumMCPStreamingManager

__all__ = [
    "create_appium_session",
    "find_elements_on_device",
    "click_element_by_text",
    "set_appium_mcp_manager",
    "get_appium_mcp_manager",
    "execute_device_action",
    "create_simulation_tools",
    "MCPAppiumClient",
    "Platform",
    "AppiumMCPStreamingManager",
]
