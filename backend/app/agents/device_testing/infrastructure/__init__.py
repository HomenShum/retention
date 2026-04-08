"""
Mobile MCP Infrastructure Module

Provides core Mobile MCP infrastructure and tools for mobile device automation:
- Mobile MCP Client (platform-agnostic mobile automation)
- Mobile MCP Streaming Manager (session management, real-time streaming)
- Device tools (actions, element finding, session creation)
- Simulation tools (test execution infrastructure)
"""

from .tools.appium_tools import (
    create_mobile_session,
    find_elements_on_device,
    click_element_by_text,
)
from .tools.device_tools import execute_device_action
from .tools.simulation_tools import create_simulation_tools
from .appium_mcp_streaming import MobileMCPStreamingManager
from .mcp_appium_client import MCPAppiumClient, Platform

__all__ = [
    "create_mobile_session",
    "find_elements_on_device",
    "click_element_by_text",
    "execute_device_action",
    "create_simulation_tools",
    "MobileMCPStreamingManager",
    "MCPAppiumClient",
    "Platform",
]
