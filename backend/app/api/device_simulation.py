"""
Device Simulation API Router

Consolidated endpoint for all device simulation operations:
- Device discovery and management
- Emulator/AVD management (launch, stop, list)
- Appium MCP session management
- AI-powered test generation and execution
- Bug reproduction automation
"""

from fastapi import APIRouter, HTTPException, Query, WebSocket, WebSocketDisconnect, Body
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from typing import Dict, Any, Optional, List, Tuple
from datetime import datetime, timezone
from pathlib import Path
import logging
import subprocess
import asyncio
import base64
import httpx
import os
import re

from ..agents.device_testing.infrastructure import MobileMCPStreamingManager
from ..agents.device_testing import (
    MobileMCPClient,
    BugReportInput,
    BugReproductionResult,
    NarratedWalkthroughService,
)
from ..agents.device_testing.cloud_providers import get_device_provider, get_provider_info

logger = logging.getLogger(__name__)

# Cache for device/emulator discovery (TTL: 2 seconds)
_DEVICE_CACHE: Dict[str, Tuple[float, Any]] = {}
_DEVICE_CACHE_TTL = 2.0

router = APIRouter(prefix="/api/device-simulation", tags=["device-simulation"])

# ============================================================================
# Dependency Injection
# ============================================================================

_android_home: Optional[str] = None
_bug_reproduction_service = None
_capabilities_config: Optional[str] = None
_mobile_mcp_client: Optional[MobileMCPClient] = None
_mobile_mcp_streaming: Optional[MobileMCPStreamingManager] = None
_screenshots_root = Path(__file__).resolve().parents[2] / "screenshots"


class WalkthroughSegmentPayload(BaseModel):
    """Client-provided narration segment payload."""

    title: str = ""
    text: str
    pause_after_ms: int = Field(default=350, ge=0)


class NarratedWalkthroughRequest(BaseModel):
    """Request body for narrated walkthrough generation."""

    device_id: str = Field(..., min_length=1)
    duration: int = Field(default=18, ge=1, le=600)
    model: str = Field(default="tts-1", min_length=1)
    voice: str = Field(default="alloy", min_length=1)
    record_size: str = Field(default="720x1280", min_length=3)
    bitrate: str = Field(default="8M", min_length=1)
    stop_when_scenario_complete: bool = True
    script: Optional[str] = None
    segments: Optional[List[WalkthroughSegmentPayload]] = None


def _default_walkthrough_segments() -> List[Dict[str, Any]]:
    """Return the default demo narration plan used by the walkthrough flow."""

    return [
        {
            "title": "Launch",
            "text": (
                "We start by launching Android Settings to capture a clean, "
                "repeatable walkthrough on the emulator."
            ),
            "pause_after_ms": 450,
        },
        {
            "title": "Navigate",
            "text": (
                "Next, we scroll through the settings list and open a detail "
                "screen, showing how the recording can follow a scripted QA demo."
            ),
            "pause_after_ms": 400,
        },
        {
            "title": "Wrap Up",
            "text": (
                "Finally, we return back out and land on the home screen. The "
                "service combines the raw recording, narration audio, subtitles, "
                "and manifest into a shareable walkthrough artifact."
            ),
            "pause_after_ms": 0,
        },
    ]


def _parse_walkthrough_script(script: str) -> List[Dict[str, Any]]:
    """Convert a textarea script into narration segment payloads."""

    blocks = [block.strip() for block in re.split(r"\n\s*\n", script) if block.strip()]
    parsed_segments: List[Dict[str, Any]] = []
    for index, block in enumerate(blocks, start=1):
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        if not lines:
            continue

        if ":" in lines[0]:
            raw_title, first_text = lines[0].split(":", 1)
            title = raw_title.strip() or f"Step {index}"
            text = " ".join(part for part in [first_text.strip(), *lines[1:]] if part)
        elif len(lines) == 1:
            title = f"Step {index}"
            text = lines[0]
        else:
            title = lines[0]
            text = " ".join(lines[1:])

        parsed_segments.append(
            {
                "title": title,
                "text": text.strip(),
                "pause_after_ms": 350,
            }
        )

    return parsed_segments


def _resolve_walkthrough_segments(request: NarratedWalkthroughRequest) -> List[Dict[str, Any]]:
    """Resolve structured segments or a textarea script into segment payloads."""

    if request.segments:
        return [segment.model_dump() for segment in request.segments]

    if request.script is not None:
        segments = _parse_walkthrough_script(request.script)
        if not segments:
            raise HTTPException(
                status_code=422,
                detail="Provide a non-empty walkthrough script or structured segments.",
            )
        return segments

    return _default_walkthrough_segments()


def _to_static_screenshots_path(path_str: Optional[str]) -> Optional[str]:
    """Convert a filesystem path under backend/screenshots into a static URL."""

    if not path_str:
        return None

    try:
        relative_path = Path(path_str).resolve().relative_to(_screenshots_root.resolve())
    except Exception:
        return None

    return f"/static/screenshots/{relative_path.as_posix()}"


def _build_walkthrough_artifact_urls(result_payload: Dict[str, Any]) -> Dict[str, Optional[str]]:
    """Build browser-consumable static URLs for walkthrough artifacts."""

    return {
        "raw_video": _to_static_screenshots_path(result_payload.get("raw_video_path")),
        "final_video": _to_static_screenshots_path(result_payload.get("final_video_path")),
        "narration_audio": _to_static_screenshots_path(
            result_payload.get("narration_audio_path")
        ),
        "subtitles": _to_static_screenshots_path(result_payload.get("subtitles_path")),
        "manifest": _to_static_screenshots_path(result_payload.get("manifest_path")),
    }


def set_android_home(android_home: str):
    """Set the Android SDK home directory"""
    global _android_home
    _android_home = android_home


def set_bug_reproduction_service(service, capabilities_config: str):
    """Set the bug reproduction service"""
    global _bug_reproduction_service, _capabilities_config
    _bug_reproduction_service = service
    _capabilities_config = capabilities_config


def set_mobile_mcp_client(client: MobileMCPClient):
    """Set the Mobile MCP client"""
    global _mobile_mcp_client
    _mobile_mcp_client = client


def get_mobile_mcp_client() -> MobileMCPClient:
    """Get the Mobile MCP client"""
    if _mobile_mcp_client is None:
        raise HTTPException(status_code=503, detail="Mobile MCP client not initialized")
    return _mobile_mcp_client


def set_mobile_mcp_streaming(streaming: MobileMCPStreamingManager):
    """Set the Mobile MCP streaming manager"""
    global _mobile_mcp_streaming
    _mobile_mcp_streaming = streaming


def get_android_home() -> str:
    """Get the Android SDK home directory"""
    if not _android_home:
        raise HTTPException(status_code=503, detail="Android SDK not configured")
    return _android_home


def get_appium_mcp_streaming():
    """Get the Appium MCP streaming manager"""
    if not _mobile_mcp_streaming:
        raise HTTPException(status_code=503, detail="Mobile MCP streaming not initialized")
    return _mobile_mcp_streaming


# ============================================================================
# CLOUD DEVICE PROVIDERS
# ============================================================================

@router.get("/providers")
async def get_cloud_providers():
    """Get information about available cloud device providers."""
    return get_provider_info()


@router.get("/providers/devices")
async def get_provider_devices():
    """Get devices from the current cloud provider."""
    try:
        provider = await get_device_provider()
        devices = await provider.list_devices()
        return {
            "provider": provider.provider_name,
            "devices": [
                {
                    "device_id": d.device_id,
                    "name": d.name,
                    "platform": d.platform,
                    "status": d.status.value,
                    "os_version": d.os_version,
                    "screen_width": d.screen_width,
                    "screen_height": d.screen_height,
                }
                for d in devices
            ],
            "count": len(devices),
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
    except Exception as e:
        logger.error(f"Error getting provider devices: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/providers/devices/start")
async def start_cloud_device(template: str = Body(..., embed=True)):
    """Start a new cloud device instance."""
    try:
        provider = await get_device_provider()
        device = await provider.start_device(template)
        return {
            "device_id": device.device_id,
            "name": device.name,
            "status": device.status.value,
            "provider": provider.provider_name
        }
    except Exception as e:
        logger.error(f"Error starting cloud device: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/providers/devices/{device_id}/stop")
async def stop_cloud_device(device_id: str):
    """Stop a cloud device instance."""
    try:
        provider = await get_device_provider()
        success = await provider.stop_device(device_id)
        return {"success": success, "device_id": device_id}
    except Exception as e:
        logger.error(f"Error stopping cloud device: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================================
# DEVICE DISCOVERY
# ============================================================================

@router.get("/devices/android")
async def get_android_devices():
    """Get list of connected Android devices."""
    try:
        import time
        now = time.time()
        cached_time, cached_result = _DEVICE_CACHE.get("android_devices", (0, None))
        if cached_result and (now - cached_time) < _DEVICE_CACHE_TTL:
            return cached_result

        # Use async subprocess to avoid blocking the event loop
        proc = await asyncio.create_subprocess_exec(
            "adb", "devices", "-l",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=5.0)

        if proc.returncode != 0:
            raise HTTPException(status_code=500, detail="Failed to get devices")

        lines = stdout.decode().strip().split("\n")[1:]
        devices = []

        for line in lines:
            if line.strip():
                parts = line.split()
                if len(parts) >= 2:
                    device_id = parts[0]
                    status = parts[1]

                    device_info = {
                        "device_id": device_id,
                        "status": status,
                        "model": "Unknown",
                        "product": "Unknown"
                    }

                    for part in parts[2:]:
                        if part.startswith("model:"):
                            device_info["model"] = part.split(":")[1]
                        elif part.startswith("product:"):
                            device_info["product"] = part.split(":")[1]

                    devices.append(device_info)

        result = {
            "devices": devices,
            "count": len(devices),
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
        
        _DEVICE_CACHE["android_devices"] = (now, result)
        return result

    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="ADB command timed out")
    except FileNotFoundError:
        raise HTTPException(status_code=503, detail="ADB not found. Please install Android SDK.")
    except Exception as e:
        logger.error(f"Error getting devices: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================================
# EMULATOR MANAGEMENT
# ============================================================================

@router.get("/emulators")
async def get_emulators():
    """Get list of connected Android emulators."""
    try:
        import time
        now = time.time()
        cached_time, cached_result = _DEVICE_CACHE.get("emulators", (0, None))
        if cached_result and (now - cached_time) < _DEVICE_CACHE_TTL:
            return cached_result

        # Use async subprocess to avoid blocking the event loop
        proc = await asyncio.create_subprocess_exec(
            "adb", "devices", "-l",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=5.0)

        emulators = []
        for line in stdout.decode().split('\n')[1:]:
            if 'emulator' in line.lower():
                parts = line.split()
                if len(parts) >= 2:
                    emulators.append({
                        'device_id': parts[0],
                        'status': parts[1],
                        'type': 'emulator'
                    })

        result = {
            'emulators': emulators,
            'count': len(emulators),
            'timestamp': datetime.now(timezone.utc).isoformat()
        }
        
        _DEVICE_CACHE["emulators"] = (now, result)
        return result

    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="ADB command timed out")
    except Exception as e:
        logger.error(f"Error getting emulators: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/emulators/avds")
async def list_avds():
    """List all available Android Virtual Devices (AVDs)."""
    try:
        android_home = get_android_home()
        emulator_path = os.path.join(android_home, 'emulator', 'emulator')

        result = subprocess.run(
            [emulator_path, '-list-avds'],
            capture_output=True,
            text=True,
            timeout=10
        )

        avds = [avd.strip() for avd in result.stdout.strip().split('\n') if avd.strip()]

        return {
            'avds': avds,
            'count': len(avds),
            'timestamp': datetime.now(timezone.utc).isoformat()
        }

    except Exception as e:
        logger.error(f"Error listing AVDs: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/emulators/launch")
async def launch_emulator(
    avd_name: Optional[str] = Query(None, description="Name of the AVD to launch (auto-selected if not provided)"),
    count: int = Query(1, ge=1, le=20, description="Number of emulators to launch"),
    no_snapshot: bool = Query(False, description="Start emulator without loading snapshot"),
    wipe_data: bool = Query(False, description="Wipe user data before starting"),
    wait_for_boot: bool = Query(False, description="Wait for emulator to boot before returning")
):
    """Launch Android emulator(s) with smart AVD selection."""
    try:
        android_home = get_android_home()
        emulator_path = os.path.join(android_home, 'emulator', 'emulator')

        # Check if emulator exists
        if not os.path.exists(emulator_path):
            raise HTTPException(status_code=500, detail=f"Emulator not found at {emulator_path}")

        # Track whether user specified an AVD
        user_specified_avd = avd_name is not None

        # Get available AVDs - needed for both auto-selection and validation
        result = subprocess.run(
            [emulator_path, '-list-avds'],
            capture_output=True,
            text=True,
            timeout=5
        )
        avds = [line.strip() for line in result.stdout.split('\n') if line.strip()]

        if not avds:
            raise HTTPException(status_code=500, detail="No AVDs available. Please create an AVD first.")

        # Build list of stable AVDs for auto-selection
        # Prefer stable AVDs, avoid problematic ones like Foldable and Pixel_6 (boot issues)
        # Pixel_8 is known to work well, so prioritize it
        preferred_avds = ["Pixel_8_API_36", "Pixel_5_API_36", "Pixel_7_API_36", "Medium_Phone_API_36.1", "Pixel_4_API_36"]
        stable_avds = []
        for preferred in preferred_avds:
            if preferred in avds:
                stable_avds.append(preferred)

        # If no preferred AVDs found, use non-foldable AVDs
        if not stable_avds:
            for avd in avds:
                if "foldable" not in avd.lower() and "tablet" not in avd.lower():
                    stable_avds.append(avd)

        # Last resort: use all AVDs
        if not stable_avds:
            stable_avds = avds

        logger.info(f"Available stable AVDs: {stable_avds}")

        # Get current emulator count for port assignment
        adb_result = subprocess.run(
            ['adb', 'devices'],
            capture_output=True,
            text=True,
            timeout=5
        )
        current_count = len([line for line in adb_result.stdout.split('\n') if 'emulator' in line])
        logger.info(f"Currently running emulators: {current_count}")

        # Get currently running AVDs to avoid conflicts
        running_avds = set()
        for line in adb_result.stdout.split('\n'):
            if 'emulator' in line:
                device_id = line.split()[0]
                # Get AVD name for this device
                try:
                    avd_result = subprocess.run(
                        ['adb', '-s', device_id, 'emu', 'avd', 'name'],
                        capture_output=True,
                        text=True,
                        timeout=5
                    )
                    if avd_result.returncode == 0:
                        # Clean up the AVD name - remove any trailing "OK" or whitespace
                        running_avd = avd_result.stdout.strip().split('\n')[0].strip()
                        running_avds.add(running_avd)
                        logger.info(f"{device_id} is running AVD: {running_avd}")
                except Exception as e:
                    logger.warning(f"Could not get AVD name for {device_id}: {e}")

        logger.info(f"Currently running AVDs: {running_avds}")

        launched = []
        errors = []

        for i in range(count):
            # Calculate port for this emulator
            port = 5554 + (current_count + i) * 2

            # Select an AVD that's not currently running
            selected_avd = None
            use_read_only = False

            if user_specified_avd:
                # User specified an AVD - use it even if already running
                selected_avd = avd_name
                if avd_name in running_avds:
                    use_read_only = True
                    logger.warning(f"AVD {avd_name} is already running, will use -read-only flag")
            else:
                # Auto-select an AVD that's not currently running
                for avd in stable_avds:
                    if avd not in running_avds:
                        selected_avd = avd
                        running_avds.add(avd)  # Mark as used for this launch batch
                        logger.info(f"Auto-selected AVD: {selected_avd}")
                        break

                # If all AVDs are in use, use -read-only flag to allow sharing
                if not selected_avd:
                    selected_avd = stable_avds[i % len(stable_avds)]
                    use_read_only = True
                    logger.warning(f"All AVDs in use, launching {selected_avd} with -read-only flag")

            logger.info(f"Launching emulator on port {port} with AVD {selected_avd} (read-only: {use_read_only})")

            # Launch with visible window so user can see the emulator
            # Use specific settings to avoid black screen issues
            cmd = [
                emulator_path,
                '-avd', selected_avd,
                '-port', str(port),
                '-no-audio',
                '-gpu', 'swiftshader_indirect',  # Software rendering for compatibility
                '-no-snapshot',           # Completely disable snapshots to avoid black screen
            ]

            if use_read_only:
                cmd.append('-read-only')
            if no_snapshot and '-no-snapshot' not in cmd:
                cmd.append('-no-snapshot')
            if wipe_data:
                cmd.append('-wipe-data')

            try:
                process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    start_new_session=True
                )

                device_id = f"emulator-{port}"
                launched.append({
                    'avd_name': selected_avd,
                    'device_id': device_id,
                    'instance': i + 1,
                    'pid': process.pid,
                    'port': port,
                    'status': 'launching'
                })
                logger.info(f"Launched emulator: {device_id} (PID: {process.pid}) with AVD {selected_avd}")

                # Wait for emulator to boot if requested
                if wait_for_boot:
                    logger.info(f"Waiting for {device_id} to boot...")
                    boot_timeout = 120  # 2 minutes timeout
                    boot_start = asyncio.get_event_loop().time()

                    while True:
                        # Check if emulator is booted
                        check_result = subprocess.run(
                            ['adb', '-s', device_id, 'shell', 'getprop', 'sys.boot_completed'],
                            capture_output=True,
                            text=True,
                            timeout=5
                        )

                        if check_result.returncode == 0 and check_result.stdout.strip() == '1':
                            logger.info(f"{device_id} is fully booted!")
                            launched[-1]['status'] = 'ready'
                            break

                        # Check timeout
                        if asyncio.get_event_loop().time() - boot_start > boot_timeout:
                            logger.warning(f"{device_id} boot timeout after {boot_timeout}s")
                            launched[-1]['status'] = 'boot_timeout'
                            break

                        # Wait before next check
                        await asyncio.sleep(2)

                # Add delay between launches to avoid conflicts
                if i < count - 1:
                    await asyncio.sleep(3)

            except Exception as e:
                error_msg = f"Failed to launch emulator on port {port}: {e}"
                logger.error(error_msg)
                errors.append(error_msg)

        if not launched:
            raise HTTPException(status_code=500, detail=f"Failed to launch any emulators. Errors: {errors}")

        # Count ready emulators
        ready_count = sum(1 for e in launched if e.get('status') == 'ready')

        # Generate appropriate message
        if wait_for_boot:
            if ready_count == len(launched):
                message = f"Launched {len(launched)} emulator(s) and all are ready!"
            else:
                message = f"Launched {len(launched)} emulator(s). {ready_count} ready, {len(launched) - ready_count} still booting."
        else:
            message = f"Launched {len(launched)} emulator(s). They will be ready in 30-60 seconds."

        response = {
            'status': 'success',
            'launched': launched,
            'count': len(launched),
            'ready_count': ready_count,
            'message': message,
            'timestamp': datetime.now(timezone.utc).isoformat()
        }

        if errors:
            response['errors'] = errors

        return response

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error launching emulator: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/emulators/stop")
async def stop_emulator(device_id: str = Query(..., description="Device ID to stop")):
    """Stop a running emulator."""
    try:
        result = subprocess.run(
            ['adb', '-s', device_id, 'emu', 'kill'],
            capture_output=True,
            text=True,
            timeout=10
        )

        return {
            'status': 'success' if result.returncode == 0 else 'failed',
            'device_id': device_id,
            'message': result.stdout or result.stderr,
            'timestamp': datetime.now(timezone.utc).isoformat()
        }

    except Exception as e:
        logger.error(f"Error stopping emulator: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/emulators/stop-all")
async def stop_all_emulators():
    """Stop all running emulators."""
    try:
        result = subprocess.run(
            ['adb', 'devices'],
            capture_output=True,
            text=True,
            timeout=5
        )

        stopped = []
        for line in result.stdout.split('\n')[1:]:
            if 'emulator' in line.lower():
                parts = line.split()
                if len(parts) >= 1:
                    device_id = parts[0]
                    subprocess.run(['adb', '-s', device_id, 'emu', 'kill'], timeout=10)
                    stopped.append(device_id)

        return {
            'status': 'success',
            'stopped': stopped,
            'count': len(stopped),
            'timestamp': datetime.now(timezone.utc).isoformat()
        }

    except Exception as e:
        logger.error(f"Error stopping emulators: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================================
# APPIUM MCP SESSION MANAGEMENT
# ============================================================================

@router.post("/sessions/create")
async def create_appium_session(
    device_id: str = Query(..., description="Device ID"),
    app_package: Optional[str] = Query(None, description="App package to launch"),
    enable_streaming: bool = Query(False, description="Enable video streaming"),
    fps: int = Query(2, ge=1, le=10, description="Frames per second for streaming"),
    use_system_port: bool = Query(False, description="Use unique system port")
):
    """Create a new Appium MCP session."""
    try:
        manager = get_appium_mcp_streaming()
        session_id = await manager.create_session(
            device_id=device_id,
            enable_streaming=enable_streaming,
            fps=fps,
            auto_assign_system_port=use_system_port
        )

        # Optionally launch an app after session creation
        if app_package:
            try:
                await manager.execute_action(session_id, "open_app", {"package": app_package})
            except Exception as _e:
                logger.warning(f"Failed to auto-launch app '{app_package}' for session {session_id}: {_e}")

        return {
            "status": "success",
            "session_id": session_id,
            "device_id": device_id,
            "streaming_enabled": enable_streaming,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }

    except Exception as e:
        logger.error(f"Error creating session: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/sessions")
async def list_sessions():
    """List all active Appium MCP sessions."""
    try:
        manager = get_appium_mcp_streaming()
        sessions = await manager.list_sessions()

        return {
            "status": "success",
            "sessions": sessions,
            "count": len(sessions),
            "timestamp": datetime.now(timezone.utc).isoformat()
        }

    except Exception as e:
        logger.error(f"Error listing sessions: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/sessions/{session_id}/close")
async def close_session(session_id: str):
    """Close an Appium MCP session."""
    try:
        manager = get_appium_mcp_streaming()
        await manager.close_session(session_id)

        return {
            "status": "success",
            "session_id": session_id,
            "message": "Session closed successfully",
            "timestamp": datetime.now(timezone.utc).isoformat()
        }

    except Exception as e:
        logger.error(f"Error closing session: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/sessions/{session_id}/action")
async def execute_action(session_id: str, action: Dict[str, Any] = Body(...)):
    """Execute an action on an Appium MCP session."""
    try:
        manager = get_appium_mcp_streaming()
        result = await manager.execute_action(session_id, action)

        return {
            "status": "success",
            "session_id": session_id,
            "result": result,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }

    except Exception as e:
        logger.error(f"Error executing action: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/sessions/{session_id}/screenshot")
async def get_screenshot(session_id: str):
    """Get screenshot from an Appium MCP session."""
    try:
        manager = get_appium_mcp_streaming()
        screenshot_base64 = await manager.get_screenshot(session_id)

        return {
            "status": "success",
            "session_id": session_id,
            "screenshot": screenshot_base64,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }

    except Exception as e:
        logger.error(f"Error getting screenshot: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/sessions/{session_id}/page-source")
async def get_page_source(session_id: str):
    """Get page source from an Appium MCP session."""
    try:
        manager = get_appium_mcp_streaming()
        page_source = await manager.get_page_source(session_id)

        return {
            "status": "success",
            "session_id": session_id,
            "page_source": page_source,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }

    except Exception as e:
        logger.error(f"Error getting page source: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.websocket("/sessions/{session_id}/stream")
async def session_streaming_websocket(websocket: WebSocket, session_id: str):
    """WebSocket endpoint for streaming session frames."""
    await websocket.accept()
    manager = get_appium_mcp_streaming()

    try:
        logger.info(f"WebSocket connected for session {session_id}")

        while True:
            frame = await manager.get_frame(session_id)

            if frame:
                await websocket.send_json({
                    "session_id": session_id,
                    "frame": frame,
                    "timestamp": datetime.now(timezone.utc).isoformat()
                })

            await asyncio.sleep(0.5)

    except WebSocketDisconnect:
        logger.info(f"WebSocket disconnected for session {session_id}")
    except Exception as e:
        logger.error(f"WebSocket error for session {session_id}: {e}")


# ============================================================================
# AI-POWERED TEST GENERATION
# ============================================================================

@router.post("/ai/generate-locators")
async def generate_locators(
    session_id: str = Query(..., description="Appium session ID"),
    element_description: str = Query(..., description="Description of element to find")
):
    """Generate locators for UI elements using AI."""
    try:
        manager = get_appium_mcp_streaming()

        page_source = await manager.get_page_source(session_id)
        screenshot = await manager.get_screenshot(session_id)

        prompt = f"""Given this Android UI hierarchy and screenshot, generate locators for: {element_description}

UI Hierarchy:
{page_source[:5000]}

Provide multiple locator strategies (id, xpath, accessibility id, etc.) ranked by reliability."""

        locators = {
            "element_description": element_description,
            "suggested_locators": [
                {"strategy": "id", "value": "com.example:id/button", "confidence": 0.9},
                {"strategy": "xpath", "value": "//android.widget.Button[@text='Click']", "confidence": 0.8},
                {"strategy": "accessibility_id", "value": "submit_button", "confidence": 0.7}
            ],
            "page_source_length": len(page_source),
            "has_screenshot": bool(screenshot)
        }

        return {
            "status": "success",
            "session_id": session_id,
            "locators": locators,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }

    except Exception as e:
        logger.error(f"Error generating locators: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/ai/generate-test")
async def generate_test(
    session_id: str = Query(..., description="Appium session ID"),
    test_description: str = Query(..., description="Description of test to generate")
):
    """Generate test code using AI."""
    try:
        manager = get_appium_mcp_streaming()

        page_source = await manager.get_page_source(session_id)

        test_code = f"""# Auto-generated test: {test_description}
def test_{test_description.lower().replace(' ', '_')}(driver):
    # TODO: Implement test steps
    pass
"""

        return {
            "status": "success",
            "session_id": session_id,
            "test_code": test_code,
            "test_description": test_description,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }

    except Exception as e:
        logger.error(f"Error generating test: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================================
# BUG REPRODUCTION
# ============================================================================

@router.post("/bugs/reproduce")
async def reproduce_bug(bug_report: BugReportInput) -> BugReproductionResult:
    """Automatically reproduce a bug using AI agent."""
    try:
        if not _bug_reproduction_service:
            raise HTTPException(status_code=503, detail="Bug reproduction service not initialized")

        appium_url = "http://localhost:4723/wd/hub"
        capabilities = {
            "platformName": "Android",
            "deviceName": bug_report.device_id,
            "automationName": "UiAutomator2",
            "noReset": True,
            "newCommandTimeout": 300
        }

        if bug_report.app_package:
            capabilities["appPackage"] = bug_report.app_package
            capabilities["appActivity"] = bug_report.app_activity or ".MainActivity"

        result = await _bug_reproduction_service.reproduce_bug(
            bug_report=bug_report,
            appium_url=appium_url,
            capabilities=capabilities
        )

        return result

    except Exception as e:
        logger.error(f"Error reproducing bug: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/bugs/reports")
async def list_bug_reports():
    """List all bug reproduction reports."""
    try:
        if not _bug_reproduction_service:
            raise HTTPException(status_code=503, detail="Bug reproduction service not initialized")

        reports_dir = Path("backend/data/bug_reports")
        if not reports_dir.exists():
            return {"reports": [], "count": 0}

        reports = []
        for report_file in reports_dir.glob("*.json"):
            reports.append({
                "filename": report_file.name,
                "created": datetime.fromtimestamp(report_file.stat().st_ctime, tz=timezone.utc).isoformat()
            })

        return {
            "reports": sorted(reports, key=lambda x: x["created"], reverse=True),
            "count": len(reports),
            "timestamp": datetime.now(timezone.utc).isoformat()
        }

    except Exception as e:
        logger.error(f"Error listing bug reports: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/bugs/reports/{report_id}")
async def get_bug_report(report_id: str):
    """Get a specific bug reproduction report."""
    try:
        report_path = Path(f"backend/data/bug_reports/{report_id}.json")
        if not report_path.exists():
            raise HTTPException(status_code=404, detail="Report not found")

        return FileResponse(report_path, media_type="application/json")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting bug report: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/bugs/screenshots/{screenshot_name}")
async def get_bug_screenshot(screenshot_name: str):
    """Get a screenshot from a bug reproduction."""
    try:
        screenshot_path = Path(f"backend/bug_screenshots/{screenshot_name}")
        if not screenshot_path.exists():
            raise HTTPException(status_code=404, detail="Screenshot not found")

        return FileResponse(screenshot_path, media_type="image/png")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting screenshot: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/walkthroughs/narrated")
async def create_narrated_walkthrough(request: NarratedWalkthroughRequest):
    """Generate a narrated walkthrough artifact bundle for a device."""

    try:
        segments = _resolve_walkthrough_segments(request)
        service = NarratedWalkthroughService(
            device_id=request.device_id,
            model=request.model,
            voice=request.voice,
        )
        result = await service.generate_walkthrough(
            segments=segments,
            duration=request.duration,
            record_size=request.record_size,
            bitrate=request.bitrate,
            stop_when_scenario_complete=request.stop_when_scenario_complete,
        )
        result_payload = result.to_dict()
        return {
            "success": True,
            "device_id": request.device_id,
            "segments_count": len(result_payload.get("segments", [])),
            "result": result_payload,
            "artifact_urls": _build_walkthrough_artifact_urls(result_payload),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        logger.error(f"Error generating narrated walkthrough: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================================
# MOBILE MCP ENDPOINTS
# ============================================================================

@router.get("/mobile-mcp/devices")
async def mobile_list_devices():
    """List all available iOS simulators, Android emulators, and physical devices (Mobile MCP)."""
    try:
        client = get_mobile_mcp_client()
        devices_text = await client.list_available_devices()

        # Parse the device list from text format
        # Expected format: "Found these devices:\nAndroid devices: [emulator-5554, ...]\niOS devices: [...]"
        android_devices = []
        ios_devices = []

        if "Android devices:" in devices_text:
            android_part = devices_text.split("Android devices:")[1].split("\n")[0]
            # Extract devices from [device1, device2, ...]
            android_part = android_part.strip().strip("[]")
            if android_part:
                android_devices = [d.strip() for d in android_part.split(",")]

        if "iOS devices:" in devices_text:
            ios_part = devices_text.split("iOS devices:")[1].split("\n")[0]
            ios_part = ios_part.strip().strip("[]")
            if ios_part:
                ios_devices = [d.strip() for d in ios_part.split(",")]

        return {
            "devices": {
                "android": android_devices,
                "ios": ios_devices
            },
            "raw_output": devices_text,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
    except Exception as e:
        logger.error(f"Error listing devices via Mobile MCP: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/mobile-mcp/devices/{device_id}/apps")
async def mobile_list_apps(device_id: str):
    """List all installed apps on a device (Mobile MCP)."""
    try:
        client = get_mobile_mcp_client()
        apps = await client.list_apps(device_id)
        return {
            "device_id": device_id,
            "apps": apps,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
    except Exception as e:
        logger.error(f"Error listing apps via Mobile MCP: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/mobile-mcp/devices/{device_id}/apps/launch")
async def mobile_launch_app(device_id: str, package_name: str = Body(..., embed=True)):
    """Launch an app on a device (Mobile MCP)."""
    try:
        client = get_mobile_mcp_client()
        result = await client.launch_app(device_id, package_name)
        return {
            "success": True,
            "device_id": device_id,
            "package_name": package_name,
            "result": result,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
    except Exception as e:
        logger.error(f"Error launching app via Mobile MCP: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/mobile-mcp/devices/{device_id}/apps/terminate")
async def mobile_terminate_app(device_id: str, package_name: str = Body(..., embed=True)):
    """Terminate an app on a device (Mobile MCP)."""
    try:
        client = get_mobile_mcp_client()
        result = await client.terminate_app(device_id, package_name)
        return {
            "device_id": device_id,
            "package_name": package_name,
            "result": result,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
    except Exception as e:
        logger.error(f"Error terminating app via Mobile MCP: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/mobile-mcp/devices/{device_id}/screenshot")
async def mobile_take_screenshot(device_id: str):
    """Take a screenshot of a device (Mobile MCP)."""
    try:
        client = get_mobile_mcp_client()
        screenshot = await client.take_screenshot(device_id)
        return {
            "device_id": device_id,
            "screenshot": screenshot,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
    except Exception as e:
        logger.error(f"Error taking screenshot via Mobile MCP: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/mobile-mcp/devices/{device_id}/elements")
async def mobile_list_elements(device_id: str):
    """List all interactive elements on screen (Mobile MCP)."""
    try:
        client = get_mobile_mcp_client()
        elements = await client.list_elements_on_screen(device_id)
        return {
            "device_id": device_id,
            "elements": elements,
            "count": len(elements),
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
    except Exception as e:
        logger.error(f"Error listing elements via Mobile MCP: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/mobile-mcp/devices/{device_id}/click")
async def mobile_click(device_id: str, x: int = Body(...), y: int = Body(...)):
    """Click at coordinates on a device (Mobile MCP)."""
    try:
        client = get_mobile_mcp_client()
        result = await client.click_on_screen(device_id, x, y)
        return {
            "success": True,
            "device_id": device_id,
            "x": x,
            "y": y,
            "result": result,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
    except Exception as e:
        logger.error(f"Error clicking via Mobile MCP: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/mobile-mcp/devices/{device_id}/swipe")
async def mobile_swipe(
    device_id: str,
    direction: str = Body(...),
    x: Optional[int] = Body(None),
    y: Optional[int] = Body(None),
    distance: Optional[int] = Body(None)
):
    """Swipe on a device screen (Mobile MCP)."""
    try:
        client = get_mobile_mcp_client()
        result = await client.swipe_on_screen(device_id, direction, x, y, distance)
        return {
            "success": True,
            "device_id": device_id,
            "direction": direction,
            "result": result,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
    except Exception as e:
        logger.error(f"Error swiping via Mobile MCP: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/mobile-mcp/devices/{device_id}/type")
async def mobile_type(device_id: str, text: str = Body(...), submit: bool = Body(False)):
    """Type text on a device (Mobile MCP)."""
    try:
        client = get_mobile_mcp_client()
        result = await client.type_keys(device_id, text, submit)
        return {
            "success": True,
            "device_id": device_id,
            "text": text,
            "submit": submit,
            "result": result,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
    except Exception as e:
        logger.error(f"Error typing via Mobile MCP: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/mobile-mcp/devices/{device_id}/button")
async def mobile_press_button(device_id: str, payload: Any = Body(...)):
    """Press a button on a device (Mobile MCP).

    Backwards compatible payloads:
    - JSON string: "HOME"
    - JSON object: {"button": "HOME"}
    """
    try:
        if isinstance(payload, dict):
            button = payload.get("button")
        else:
            button = payload

        if not isinstance(button, str) or not button.strip():
            raise HTTPException(status_code=422, detail="Button must be a non-empty string")

        client = get_mobile_mcp_client()
        result = await client.press_button(device_id, button)
        return {
            "success": True,
            "device_id": device_id,
            "button": button,
            "result": result,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error pressing button via Mobile MCP: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================================
# EMULATOR PIPELINE HEALTH CHECK
# ============================================================================

@router.get("/health/emulator")
async def emulator_health():
    """Verify the emulator pipeline is ready: ADB, device, ActionSpan, FFmpeg."""
    from ..agents.device_testing.action_span_service import action_span_service

    checks: Dict[str, Any] = {}
    overall_ready = True

    # --- 1. ADB available? ---
    adb_path = None
    try:
        proc = await asyncio.create_subprocess_exec(
            "adb", "version",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5.0)
        if proc.returncode == 0:
            version_line = stdout.decode().strip().split("\n")[0]
            adb_path = "adb"
            checks["adb"] = {"available": True, "version": version_line}
        else:
            checks["adb"] = {"available": False, "error": "adb returned non-zero exit code"}
            overall_ready = False
    except FileNotFoundError:
        checks["adb"] = {"available": False, "error": "adb binary not found on PATH"}
        overall_ready = False
    except asyncio.TimeoutError:
        checks["adb"] = {"available": False, "error": "adb version timed out"}
        overall_ready = False
    except Exception as e:
        checks["adb"] = {"available": False, "error": str(e)}
        overall_ready = False

    # --- 2. Emulator / device connected? ---
    if adb_path:
        try:
            proc = await asyncio.create_subprocess_exec(
                "adb", "devices",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5.0)
            devices = []
            for line in stdout.decode().strip().split("\n")[1:]:
                parts = line.split()
                if len(parts) >= 2 and parts[1] == "device":
                    devices.append(parts[0])
            checks["emulator"] = {
                "connected": len(devices) > 0,
                "devices": devices,
                "count": len(devices),
            }
            if not devices:
                overall_ready = False
        except Exception as e:
            checks["emulator"] = {"connected": False, "error": str(e)}
            overall_ready = False
    else:
        checks["emulator"] = {"connected": False, "error": "ADB not available, cannot check devices"}
        overall_ready = False

    # --- 3. ActionSpan service initialized? ---
    try:
        span_ok = action_span_service is not None
        checks["action_span_service"] = {
            "initialized": span_ok,
            "adb_available": action_span_service.adb is not None if span_ok else False,
            "clip_dir": str(action_span_service.clip_dir) if span_ok else None,
            "default_device": action_span_service._default_device_id if span_ok else None,
        }
        if not span_ok:
            overall_ready = False
    except Exception as e:
        checks["action_span_service"] = {"initialized": False, "error": str(e)}
        overall_ready = False

    # --- 4. FFmpeg available? ---
    try:
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-version",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5.0)
        if proc.returncode == 0:
            version_line = stdout.decode().strip().split("\n")[0]
            checks["ffmpeg"] = {"available": True, "version": version_line}
        else:
            checks["ffmpeg"] = {"available": False, "error": "ffmpeg returned non-zero exit code"}
            # FFmpeg missing is non-fatal: screenshot-only scoring still works
    except FileNotFoundError:
        checks["ffmpeg"] = {"available": False, "error": "ffmpeg binary not found on PATH"}
    except asyncio.TimeoutError:
        checks["ffmpeg"] = {"available": False, "error": "ffmpeg -version timed out"}
    except Exception as e:
        checks["ffmpeg"] = {"available": False, "error": str(e)}

    return {
        "ready": overall_ready,
        "checks": checks,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

