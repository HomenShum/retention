"""Device Leasing API — lease local emulators (and future cloud devices) for testing.

Routes:
    GET    /api/devices/available        — list available devices
    POST   /api/devices/lease            — lease a device
    DELETE  /api/devices/lease/{lease_id} — release a leased device
    POST   /api/devices/provision        — spin up a new emulator
"""

from __future__ import annotations

import logging
import os
import subprocess
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/devices", tags=["devices"])

# ---------------------------------------------------------------------------
# ADB helper
# ---------------------------------------------------------------------------

ADB_PATHS = [
    "adb",
    os.path.expanduser("~/Library/Android/sdk/platform-tools/adb"),
    "/usr/local/bin/adb",
    "/opt/android-sdk/platform-tools/adb",
]


def _find_adb() -> Optional[str]:
    for path in ADB_PATHS:
        try:
            result = subprocess.run(
                [path, "version"], capture_output=True, timeout=2
            )
            if result.returncode == 0:
                return path
        except Exception:
            pass
    return None


def _get_adb_devices() -> List[Dict[str, str]]:
    """Return list of {device_id, status} from ``adb devices``."""
    adb = _find_adb()
    if not adb:
        return []
    try:
        result = subprocess.run(
            [adb, "devices", "-l"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        devices: List[Dict[str, str]] = []
        for line in result.stdout.strip().splitlines()[1:]:
            parts = line.split()
            if len(parts) >= 2:
                device_id = parts[0]
                status = parts[1]  # "device", "offline", etc.
                # Try to extract model info from the rest
                model = ""
                for part in parts[2:]:
                    if part.startswith("model:"):
                        model = part.split(":", 1)[1]
                devices.append(
                    {"device_id": device_id, "status": status, "model": model}
                )
        return devices
    except Exception as exc:
        logger.error("Failed to list ADB devices: %s", exc)
        return []


def _get_device_properties(device_id: str) -> Dict[str, str]:
    """Fetch a few useful properties from a connected device via adb shell."""
    adb = _find_adb()
    if not adb:
        return {}
    props: Dict[str, str] = {}
    keys = {
        "ro.build.version.sdk": "api_level",
        "ro.build.version.release": "os_version",
        "ro.product.model": "device_model",
        "ro.product.brand": "brand",
    }
    for prop_key, mapped in keys.items():
        try:
            result = subprocess.run(
                [adb, "-s", device_id, "shell", "getprop", prop_key],
                capture_output=True,
                text=True,
                timeout=3,
            )
            if result.returncode == 0:
                props[mapped] = result.stdout.strip()
        except Exception:
            pass
    return props


# ---------------------------------------------------------------------------
# In-memory lease store
# ---------------------------------------------------------------------------

_active_leases: Dict[str, Dict[str, Any]] = {}

DEFAULT_LEASE_MINUTES = 30


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class DeviceInfo(BaseModel):
    device_id: str
    device_type: str = "local_emulator"
    status: str
    model: str = ""
    specs: Dict[str, str] = Field(default_factory=dict)
    connection: Dict[str, Any] = Field(default_factory=dict)
    leased: bool = False
    lease_id: Optional[str] = None


class LeaseRequest(BaseModel):
    device_id: str
    duration_minutes: int = DEFAULT_LEASE_MINUTES


class LeaseResponse(BaseModel):
    lease_id: str
    device_id: str
    device_type: str
    specs: Dict[str, str]
    status: str
    leased_at: str
    expires_at: str
    connection: Dict[str, Any]


class ProvisionRequest(BaseModel):
    avd_name: str = "test_device"
    api_level: int = 34
    device_profile: str = "pixel_8"


class ProvisionResponse(BaseModel):
    status: str
    message: str
    device_id: Optional[str] = None
    avd_name: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _connection_info(device_id: str) -> Dict[str, Any]:
    """Derive connection details from the device identifier."""
    if device_id.startswith("emulator-"):
        port = int(device_id.split("-")[1])
        return {"type": "adb", "host": "localhost", "port": port}
    # Physical or remote device
    if ":" in device_id:
        host, port_str = device_id.rsplit(":", 1)
        return {"type": "adb_tcp", "host": host, "port": int(port_str)}
    return {"type": "adb_usb", "serial": device_id}


def _device_type(device_id: str) -> str:
    if device_id.startswith("emulator-"):
        return "local_emulator"
    if ":" in device_id:
        return "remote_device"
    return "physical_device"


def _purge_expired_leases() -> None:
    """Remove leases that have passed their expiry time."""
    now = datetime.now(timezone.utc)
    expired = [
        lid
        for lid, lease in _active_leases.items()
        if datetime.fromisoformat(lease["expires_at"]) < now
    ]
    for lid in expired:
        logger.info("Auto-expired lease %s for device %s", lid, _active_leases[lid]["device_id"])
        del _active_leases[lid]


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/available", response_model=List[DeviceInfo])
async def list_available_devices():
    """List available devices (local emulators + physical devices via ADB).

    Cloud device-farm integration is stubbed — TODO: integrate with
    Firebase Test Lab / AWS Device Farm / BrowserStack.
    """
    _purge_expired_leases()
    leased_device_ids = {l["device_id"] for l in _active_leases.values()}

    raw_devices = _get_adb_devices()
    result: List[DeviceInfo] = []

    for dev in raw_devices:
        did = dev["device_id"]
        props = _get_device_properties(did) if dev["status"] == "device" else {}

        specs = {
            "os": f"android-{props.get('api_level', '?')}",
            "os_version": props.get("os_version", ""),
            "device": props.get("device_model", dev.get("model", "")),
            "brand": props.get("brand", ""),
            "api_level": props.get("api_level", ""),
        }

        is_leased = did in leased_device_ids
        lease_id = None
        if is_leased:
            for lid, lease in _active_leases.items():
                if lease["device_id"] == did:
                    lease_id = lid
                    break

        result.append(
            DeviceInfo(
                device_id=did,
                device_type=_device_type(did),
                status="leased" if is_leased else dev["status"],
                model=dev.get("model", ""),
                specs=specs,
                connection=_connection_info(did),
                leased=is_leased,
                lease_id=lease_id,
            )
        )

    # TODO: Append cloud device farm devices here
    # e.g. result.extend(_list_cloud_devices())

    if not result:
        logger.info("No ADB devices detected — returning empty list")

    return result


@router.post("/lease", response_model=LeaseResponse, status_code=201)
async def lease_device(req: LeaseRequest):
    """Lease a device for exclusive testing use."""
    _purge_expired_leases()

    # Verify device exists and is online
    adb_devices = _get_adb_devices()
    device_ids = [d["device_id"] for d in adb_devices]
    if req.device_id not in device_ids:
        raise HTTPException(
            status_code=404,
            detail=f"Device {req.device_id} not found. Available: {device_ids}",
        )

    # Check if already leased
    leased_ids = {l["device_id"] for l in _active_leases.values()}
    if req.device_id in leased_ids:
        raise HTTPException(
            status_code=409,
            detail=f"Device {req.device_id} is already leased",
        )

    now = datetime.now(timezone.utc)
    expires = now + timedelta(minutes=req.duration_minutes)
    lease_id = f"lease_{uuid.uuid4().hex[:8]}"

    props = _get_device_properties(req.device_id)
    specs = {
        "os": f"android-{props.get('api_level', '?')}",
        "os_version": props.get("os_version", ""),
        "device": props.get("device_model", ""),
        "brand": props.get("brand", ""),
        "api_level": props.get("api_level", ""),
    }

    lease = {
        "lease_id": lease_id,
        "device_id": req.device_id,
        "device_type": _device_type(req.device_id),
        "specs": specs,
        "status": "active",
        "leased_at": now.isoformat(),
        "expires_at": expires.isoformat(),
        "connection": _connection_info(req.device_id),
    }

    _active_leases[lease_id] = lease
    logger.info(
        "Leased device %s as %s (expires %s)",
        req.device_id,
        lease_id,
        expires.isoformat(),
    )
    return lease


@router.delete("/lease/{lease_id}", status_code=204)
async def release_device(lease_id: str):
    """Release a leased device."""
    if lease_id not in _active_leases:
        raise HTTPException(status_code=404, detail=f"Lease {lease_id} not found")
    device_id = _active_leases[lease_id]["device_id"]
    del _active_leases[lease_id]
    logger.info("Released lease %s for device %s", lease_id, device_id)


@router.post("/provision", response_model=ProvisionResponse)
async def provision_device(req: ProvisionRequest):
    """Spin up a new Android emulator with the requested specs.

    Requires ANDROID_HOME to be set and the AVD to already exist via
    ``avdmanager``.  This endpoint launches the emulator process and
    waits briefly for it to appear in ``adb devices``.
    """
    android_home = os.getenv("ANDROID_HOME") or os.path.expanduser(
        "~/Library/Android/sdk"
    )
    emulator_bin = os.path.join(android_home, "emulator", "emulator")

    if not os.path.isfile(emulator_bin):
        raise HTTPException(
            status_code=503,
            detail=f"Emulator binary not found at {emulator_bin}. Set ANDROID_HOME.",
        )

    # Check if the AVD exists
    try:
        result = subprocess.run(
            [emulator_bin, "-list-avds"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        available_avds = [
            line.strip() for line in result.stdout.splitlines() if line.strip()
        ]
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Cannot list AVDs: {exc}")

    if req.avd_name not in available_avds:
        raise HTTPException(
            status_code=404,
            detail=f"AVD '{req.avd_name}' not found. Available: {available_avds}",
        )

    # Launch the emulator in the background (non-blocking)
    try:
        subprocess.Popen(
            [emulator_bin, "-avd", req.avd_name, "-no-snapshot-load", "-no-audio"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        logger.info("Launched emulator AVD %s", req.avd_name)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to launch emulator: {exc}")

    # We don't wait for full boot — the caller should poll /api/devices/available
    return ProvisionResponse(
        status="launching",
        message=f"Emulator '{req.avd_name}' is starting. Poll /api/devices/available to detect when it comes online.",
        avd_name=req.avd_name,
    )
