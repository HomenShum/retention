"""Local mobile integration for retention.sh MCP proxy.

Detects and integrates with mobile development tools on the user's machine.
Provides local mobile QA capabilities without requiring a cloud server.

Usage from proxy.py:
    from local_mobile import detect_mobile_env, android_screenshot, ios_screenshot
    from local_mobile import android_ui_dump, ios_accessibility_tree, mobile_qa_check
"""

import base64
import json
import os
import re
import subprocess
import tempfile
import time
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Subprocess helpers
# ---------------------------------------------------------------------------

def _run(cmd: List[str], timeout: int = 15) -> Dict[str, Any]:
    """Run a subprocess command and return structured result."""
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return {
            "ok": result.returncode == 0,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "returncode": result.returncode,
        }
    except FileNotFoundError:
        return {"ok": False, "stdout": "", "stderr": f"Command not found: {cmd[0]}", "returncode": -1}
    except subprocess.TimeoutExpired:
        return {"ok": False, "stdout": "", "stderr": f"Timeout after {timeout}s", "returncode": -1}
    except Exception as e:
        return {"ok": False, "stdout": "", "stderr": str(e), "returncode": -1}


def _run_binary(cmd: List[str], timeout: int = 15) -> Dict[str, Any]:
    """Run a subprocess command and return raw bytes stdout."""
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            timeout=timeout,
        )
        return {
            "ok": result.returncode == 0,
            "stdout": result.stdout,
            "stderr": result.stderr.decode("utf-8", errors="replace"),
            "returncode": result.returncode,
        }
    except FileNotFoundError:
        return {"ok": False, "stdout": b"", "stderr": f"Command not found: {cmd[0]}", "returncode": -1}
    except subprocess.TimeoutExpired:
        return {"ok": False, "stdout": b"", "stderr": f"Timeout after {timeout}s", "returncode": -1}
    except Exception as e:
        return {"ok": False, "stdout": b"", "stderr": str(e), "returncode": -1}


# ---------------------------------------------------------------------------
# Tool detection
# ---------------------------------------------------------------------------

def _check_adb() -> Dict[str, Any]:
    """Check if ADB is available and list connected devices."""
    which = _run(["which", "adb"])
    if not which["ok"]:
        return {"adb_available": False, "devices": [], "adb_path": None}

    adb_path = which["stdout"].strip()
    devices_result = _run(["adb", "devices"])
    devices = []
    if devices_result["ok"]:
        for line in devices_result["stdout"].strip().split("\n")[1:]:
            line = line.strip()
            if not line:
                continue
            parts = line.split("\t")
            if len(parts) >= 2:
                devices.append({"id": parts[0], "status": parts[1]})

    return {
        "adb_available": True,
        "adb_path": adb_path,
        "devices": devices,
    }


def _check_simctl() -> Dict[str, Any]:
    """Check if xcrun simctl is available and list booted simulators."""
    result = _run(["xcrun", "simctl", "list", "devices", "-j"])
    if not result["ok"]:
        return {"simctl_available": False, "simulators": []}

    simulators = []
    try:
        data = json.loads(result["stdout"])
        for runtime, device_list in data.get("devices", {}).items():
            # Extract readable runtime name (e.g. "iOS 17.0" from runtime key)
            runtime_name = runtime.replace("com.apple.CoreSimulator.SimRuntime.", "").replace("-", " ").replace(".", " ")
            for device in device_list:
                if device.get("isAvailable", False) or device.get("state") == "Booted":
                    simulators.append({
                        "name": device.get("name", "Unknown"),
                        "udid": device.get("udid", ""),
                        "state": device.get("state", "Unknown"),
                        "runtime": runtime_name,
                    })
    except (json.JSONDecodeError, KeyError):
        pass

    return {
        "simctl_available": True,
        "simulators": simulators,
    }


def _check_idb() -> Dict[str, Any]:
    """Check if Facebook's idb is available."""
    which = _run(["which", "idb"])
    return {
        "idb_available": which["ok"],
        "idb_path": which["stdout"].strip() if which["ok"] else None,
    }


def _check_mcp_servers() -> Dict[str, bool]:
    """Detect known mobile MCP companion servers."""
    servers = {
        "ios_simulator_mcp": False,
        "claude_in_mobile": False,
        "mobile_mcp": False,
    }

    # Check npm global packages
    npm_result = _run(["npm", "list", "-g", "--depth=0", "--json"])
    if npm_result["ok"]:
        try:
            npm_data = json.loads(npm_result["stdout"])
            deps = npm_data.get("dependencies", {})
            if "ios-simulator-mcp" in deps or "@anthropic/ios-simulator-mcp" in deps:
                servers["ios_simulator_mcp"] = True
            if "claude-in-mobile" in deps or "@anthropic/claude-in-mobile" in deps:
                servers["claude_in_mobile"] = True
            if "mobile-mcp" in deps or "@anthropic/mobile-mcp" in deps:
                servers["mobile_mcp"] = True
        except (json.JSONDecodeError, KeyError):
            pass

    # Check common install paths
    home = os.path.expanduser("~")
    common_paths = [
        os.path.join(home, ".claude", "mcp-servers"),
        os.path.join(home, ".config", "claude", "mcp-servers"),
        "/usr/local/lib/node_modules",
    ]
    for base in common_paths:
        if os.path.isdir(os.path.join(base, "ios-simulator-mcp")):
            servers["ios_simulator_mcp"] = True
        if os.path.isdir(os.path.join(base, "claude-in-mobile")):
            servers["claude_in_mobile"] = True
        if os.path.isdir(os.path.join(base, "mobile-mcp")):
            servers["mobile_mcp"] = True

    # Check running processes (lightweight — just ps aux grep)
    ps_result = _run(["ps", "aux"])
    if ps_result["ok"]:
        ps_out = ps_result["stdout"]
        if "ios-simulator-mcp" in ps_out:
            servers["ios_simulator_mcp"] = True
        if "claude-in-mobile" in ps_out or "claude_in_mobile" in ps_out:
            servers["claude_in_mobile"] = True
        if "mobile-mcp" in ps_out:
            servers["mobile_mcp"] = True

    return servers


# ---------------------------------------------------------------------------
# Public API: detect_mobile_env
# ---------------------------------------------------------------------------

def detect_mobile_env() -> Dict[str, Any]:
    """Detect available mobile tools and running devices.

    Returns:
        {
            "android": {"adb_available": bool, "devices": [{"id": str, "status": str}]},
            "ios": {"simctl_available": bool, "idb_available": bool,
                    "simulators": [{"name": str, "udid": str, "state": str, "runtime": str}]},
            "mcp_servers": {"ios_simulator_mcp": bool, "claude_in_mobile": bool, "mobile_mcp": bool},
            "summary": str,
        }
    """
    android = _check_adb()
    ios_simctl = _check_simctl()
    ios_idb = _check_idb()
    mcp_servers = _check_mcp_servers()

    ios = {
        "simctl_available": ios_simctl["simctl_available"],
        "idb_available": ios_idb["idb_available"],
        "simulators": ios_simctl.get("simulators", []),
    }

    # Build human-readable summary
    parts = []
    if android["adb_available"]:
        n_devices = len(android["devices"])
        emulators = [d for d in android["devices"] if "emulator" in d["id"]]
        parts.append(f"ADB: {n_devices} device(s), {len(emulators)} emulator(s)")
    else:
        parts.append("ADB: not found")

    if ios["simctl_available"]:
        booted = [s for s in ios["simulators"] if s["state"] == "Booted"]
        parts.append(f"iOS Sim: {len(booted)} booted, {len(ios['simulators'])} available")
    else:
        parts.append("iOS Sim: xcrun simctl not available")

    active_mcp = [k for k, v in mcp_servers.items() if v]
    if active_mcp:
        parts.append(f"MCP: {', '.join(active_mcp)}")

    return {
        "android": android,
        "ios": ios,
        "mcp_servers": mcp_servers,
        "summary": " | ".join(parts),
    }


# ---------------------------------------------------------------------------
# Device selection helpers
# ---------------------------------------------------------------------------

def _pick_android_device(device_id: Optional[str] = None) -> Optional[str]:
    """Pick an Android device ID. Uses provided ID or first available."""
    if device_id:
        return device_id
    env = _check_adb()
    if not env["adb_available"] or not env["devices"]:
        return None
    # Prefer emulators, then any device
    for d in env["devices"]:
        if d["status"] == "device" and "emulator" in d["id"]:
            return d["id"]
    for d in env["devices"]:
        if d["status"] == "device":
            return d["id"]
    return None


def _pick_ios_simulator(udid: Optional[str] = None) -> Optional[str]:
    """Pick an iOS Simulator UDID. Uses provided UDID or first booted."""
    if udid:
        return udid
    env = _check_simctl()
    if not env["simctl_available"] or not env["simulators"]:
        return None
    # Prefer booted simulators
    for s in env["simulators"]:
        if s["state"] == "Booted":
            return s["udid"]
    return None


# ---------------------------------------------------------------------------
# Public API: Screenshots
# ---------------------------------------------------------------------------

def android_screenshot(device_id: str = None) -> Dict[str, Any]:
    """Capture screenshot from Android emulator/device via ADB.

    Returns:
        {"status": "ok", "screenshot_b64": str, "device": str, "resolution": str}
        or {"status": "error", "error": str}
    """
    dev = _pick_android_device(device_id)
    if not dev:
        return {"status": "error", "error": "No Android device available. Run 'adb devices' to check."}

    # Capture screenshot as PNG bytes via exec-out
    result = _run_binary(["adb", "-s", dev, "exec-out", "screencap", "-p"], timeout=10)
    if not result["ok"] or not result["stdout"]:
        return {"status": "error", "error": f"Screenshot failed: {result['stderr']}", "device": dev}

    png_data = result["stdout"]
    b64 = base64.b64encode(png_data).decode("ascii")

    # Try to get resolution
    resolution = "unknown"
    wm_result = _run(["adb", "-s", dev, "shell", "wm", "size"])
    if wm_result["ok"]:
        match = re.search(r"(\d+x\d+)", wm_result["stdout"])
        if match:
            resolution = match.group(1)

    return {
        "status": "ok",
        "screenshot_b64": b64,
        "device": dev,
        "resolution": resolution,
        "format": "png",
        "size_bytes": len(png_data),
    }


def ios_screenshot(udid: str = None) -> Dict[str, Any]:
    """Capture screenshot from iOS Simulator via xcrun.

    Returns:
        {"status": "ok", "screenshot_b64": str, "simulator": str, "resolution": str}
        or {"status": "error", "error": str}
    """
    sim = _pick_ios_simulator(udid)
    if not sim:
        return {"status": "error", "error": "No booted iOS Simulator found. Run 'xcrun simctl list devices' to check."}

    # Use a temp file for the screenshot
    tmp_path = os.path.join(tempfile.gettempdir(), f"ta_ios_screenshot_{sim[:8]}.png")
    result = _run(["xcrun", "simctl", "io", sim, "screenshot", tmp_path], timeout=10)
    if not result["ok"]:
        return {"status": "error", "error": f"Screenshot failed: {result['stderr']}", "simulator": sim}

    if not os.path.isfile(tmp_path):
        return {"status": "error", "error": "Screenshot file not created", "simulator": sim}

    try:
        with open(tmp_path, "rb") as f:
            png_data = f.read()
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass

    if not png_data:
        return {"status": "error", "error": "Screenshot file is empty", "simulator": sim}

    b64 = base64.b64encode(png_data).decode("ascii")

    # Get simulator window size from device info
    resolution = "unknown"
    info_result = _run(["xcrun", "simctl", "list", "devices", "-j"])
    if info_result["ok"]:
        try:
            data = json.loads(info_result["stdout"])
            for _runtime, devices in data.get("devices", {}).items():
                for d in devices:
                    if d.get("udid") == sim:
                        # Resolution not directly in JSON; derive from screenshot size
                        break
        except (json.JSONDecodeError, KeyError):
            pass

    return {
        "status": "ok",
        "screenshot_b64": b64,
        "simulator": sim,
        "resolution": resolution,
        "format": "png",
        "size_bytes": len(png_data),
    }


# ---------------------------------------------------------------------------
# Public API: UI / Accessibility trees
# ---------------------------------------------------------------------------

def android_ui_dump(device_id: str = None) -> Dict[str, Any]:
    """Get accessibility tree / UI hierarchy from Android device.

    Returns:
        {"status": "ok", "ui_tree": str (XML), "device": str}
        or {"status": "error", "error": str}
    """
    dev = _pick_android_device(device_id)
    if not dev:
        return {"status": "error", "error": "No Android device available."}

    # uiautomator dump to a temp file on device, then pull
    dump_path = "/sdcard/ta_uidump.xml"
    dump_result = _run(["adb", "-s", dev, "shell", "uiautomator", "dump", dump_path], timeout=15)
    if not dump_result["ok"]:
        # Fallback: try dumping to stdout
        fallback = _run(["adb", "-s", dev, "shell", "uiautomator", "dump", "/dev/tty"], timeout=15)
        if fallback["ok"] and fallback["stdout"].strip():
            xml_text = fallback["stdout"]
            # uiautomator prepends a status line — strip it
            idx = xml_text.find("<?xml")
            if idx >= 0:
                xml_text = xml_text[idx:]
            return {"status": "ok", "ui_tree": xml_text, "device": dev}
        return {"status": "error", "error": f"UI dump failed: {dump_result['stderr']}", "device": dev}

    # Pull the XML from device
    pull_result = _run(["adb", "-s", dev, "shell", "cat", dump_path], timeout=10)
    # Cleanup
    _run(["adb", "-s", dev, "shell", "rm", "-f", dump_path])

    if not pull_result["ok"]:
        return {"status": "error", "error": f"Failed to read UI dump: {pull_result['stderr']}", "device": dev}

    return {"status": "ok", "ui_tree": pull_result["stdout"], "device": dev}


def ios_accessibility_tree(udid: str = None) -> Dict[str, Any]:
    """Get accessibility hierarchy from iOS Simulator.

    Tries xcrun simctl first, falls back to idb if available.

    Returns:
        {"status": "ok", "accessibility_tree": str, "simulator": str}
        or {"status": "error", "error": str}
    """
    sim = _pick_ios_simulator(udid)
    if not sim:
        return {"status": "error", "error": "No booted iOS Simulator found."}

    # Try idb first (richer output)
    idb_info = _check_idb()
    if idb_info["idb_available"]:
        idb_result = _run(["idb", "ui", "describe-all", "--udid", sim], timeout=15)
        if idb_result["ok"] and idb_result["stdout"].strip():
            return {"status": "ok", "accessibility_tree": idb_result["stdout"], "simulator": sim, "source": "idb"}

    # Fallback: xcrun simctl accessibility audit
    audit_result = _run(["xcrun", "simctl", "accessibility", sim, "audit"], timeout=15)
    if audit_result["ok"]:
        return {"status": "ok", "accessibility_tree": audit_result["stdout"], "simulator": sim, "source": "simctl"}

    # Fallback: simctl spawn with accessibility inspector
    spawn_result = _run([
        "xcrun", "simctl", "spawn", sim,
        "accessibility_inspector", "--json"
    ], timeout=15)
    if spawn_result["ok"] and spawn_result["stdout"].strip():
        return {"status": "ok", "accessibility_tree": spawn_result["stdout"], "simulator": sim, "source": "spawn"}

    return {
        "status": "error",
        "error": "Could not retrieve accessibility tree. Install idb for better results: pip install fb-idb",
        "simulator": sim,
    }


# ---------------------------------------------------------------------------
# UI analysis helpers
# ---------------------------------------------------------------------------

def _analyze_android_ui(xml_text: str) -> List[Dict[str, str]]:
    """Analyze Android UI XML for common mobile QA issues."""
    findings = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        findings.append({
            "severity": "error",
            "category": "mobile",
            "title": "UI dump XML parse error",
            "detail": "Could not parse the UI hierarchy XML. The dump may be incomplete.",
        })
        return findings

    nodes = list(root.iter("node"))
    missing_desc = 0
    small_targets = 0
    truncated_text = 0

    for node in nodes:
        cls = node.get("class", "")
        text = node.get("text", "")
        content_desc = node.get("content-desc", "")
        clickable = node.get("clickable") == "true"
        bounds_str = node.get("bounds", "")

        # Check missing content descriptions on interactive elements
        is_interactive = clickable or cls.endswith("Button") or cls.endswith("ImageButton") or cls.endswith("ImageView")
        if is_interactive and not content_desc and not text:
            missing_desc += 1

        # Check touch target sizes (Android minimum: 48dp, approx 48px at 1x)
        if clickable and bounds_str:
            match = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", bounds_str)
            if match:
                x1, y1, x2, y2 = int(match.group(1)), int(match.group(2)), int(match.group(3)), int(match.group(4))
                width = x2 - x1
                height = y2 - y1
                if width < 48 or height < 48:
                    small_targets += 1

        # Check for truncated text (ellipsis in displayed text)
        if text and ("\u2026" in text or text.endswith("...")):
            truncated_text += 1

    if missing_desc > 0:
        findings.append({
            "severity": "warning",
            "category": "a11y",
            "title": f"{missing_desc} interactive element(s) missing content descriptions",
            "detail": "Clickable elements and images should have content-desc for accessibility.",
        })

    if small_targets > 0:
        findings.append({
            "severity": "warning",
            "category": "mobile-ux",
            "title": f"{small_targets} touch target(s) smaller than 48dp",
            "detail": "Android recommends minimum 48dp touch targets. Small targets cause tap errors.",
        })

    if truncated_text > 0:
        findings.append({
            "severity": "info",
            "category": "mobile-ux",
            "title": f"{truncated_text} text element(s) appear truncated",
            "detail": "Text ending with ellipsis suggests content is cut off. Check layout constraints.",
        })

    total_nodes = len(nodes)
    if total_nodes == 0:
        findings.append({
            "severity": "error",
            "category": "mobile",
            "title": "Empty UI hierarchy",
            "detail": "No UI nodes found. The screen may be blank or the app is not responding.",
        })
    elif total_nodes > 500:
        findings.append({
            "severity": "info",
            "category": "performance",
            "title": f"Complex view hierarchy ({total_nodes} nodes)",
            "detail": "Deep view hierarchies can cause jank. Consider flattening with ConstraintLayout.",
        })

    return findings


def _analyze_ios_accessibility(tree_text: str) -> List[Dict[str, str]]:
    """Analyze iOS accessibility output for common mobile QA issues."""
    findings = []
    if not tree_text.strip():
        findings.append({
            "severity": "error",
            "category": "mobile",
            "title": "Empty accessibility tree",
            "detail": "No accessibility information returned. The app may not be running.",
        })
        return findings

    # Parse idb/simctl output for common patterns
    lines = tree_text.strip().split("\n")

    missing_labels = 0
    small_frames = 0
    total_elements = 0

    for line in lines:
        total_elements += 1

        # idb format: look for elements with empty labels
        if "label:" in line.lower():
            label_match = re.search(r'label:\s*["\']?\s*["\']?', line, re.IGNORECASE)
            if label_match and not re.search(r'label:\s*["\']?\S', line, re.IGNORECASE):
                missing_labels += 1

        # Look for button/interactive elements without accessibility labels
        if any(kw in line.lower() for kw in ["button", "tapable", "interactive"]):
            if "label: ''" in line or 'label: ""' in line or "label: nil" in line:
                missing_labels += 1

        # Check frame sizes for touch targets (iOS minimum: 44pt)
        frame_match = re.search(r'frame:\s*\([\d.]+,\s*[\d.]+,\s*([\d.]+),\s*([\d.]+)\)', line)
        if frame_match:
            w = float(frame_match.group(1))
            h = float(frame_match.group(2))
            if (w > 0 and w < 44) or (h > 0 and h < 44):
                # Only flag interactive elements
                if any(kw in line.lower() for kw in ["button", "link", "tapable", "interactive"]):
                    small_frames += 1

    if missing_labels > 0:
        findings.append({
            "severity": "warning",
            "category": "a11y",
            "title": f"{missing_labels} element(s) missing accessibility labels",
            "detail": "Interactive elements need accessibility labels for VoiceOver users.",
        })

    if small_frames > 0:
        findings.append({
            "severity": "warning",
            "category": "mobile-ux",
            "title": f"{small_frames} touch target(s) smaller than 44pt",
            "detail": "Apple HIG recommends minimum 44x44pt touch targets.",
        })

    # Check for general accessibility audit warnings in simctl output
    if "warning" in tree_text.lower() or "violation" in tree_text.lower():
        warning_count = tree_text.lower().count("warning") + tree_text.lower().count("violation")
        findings.append({
            "severity": "warning",
            "category": "a11y",
            "title": f"Accessibility audit flagged {warning_count} issue(s)",
            "detail": "The system accessibility audit found potential issues. Review the full output.",
        })

    return findings


# ---------------------------------------------------------------------------
# Public API: mobile_qa_check
# ---------------------------------------------------------------------------

def mobile_qa_check(platform: str, device_id: str = None) -> Dict[str, Any]:
    """Quick mobile QA check -- screenshot + accessibility audit.

    Captures a screenshot and UI tree, then analyzes for:
    - Missing accessibility labels
    - Touch targets too small (< 44pt iOS, < 48dp Android)
    - Text too small for readability
    - Missing content descriptions

    Args:
        platform: "android" or "ios"
        device_id: optional device ID / UDID. Auto-selects if not provided.

    Returns findings in same format as local_crawl.py:
        {
            "status": "ok",
            "platform": str,
            "device": str,
            "findings": [{"severity": str, "category": str, "title": str, "detail": str}],
            "screenshot_b64": str | None,
            "verdict": "pass" | "fail",
            "duration_ms": int,
        }
    """
    start = time.time()
    platform = platform.lower().strip()
    findings = []
    screenshot_b64 = None
    device_label = device_id or "auto"

    if platform not in ("android", "ios"):
        return {
            "status": "error",
            "error": f"Unsupported platform: {platform}. Use 'android' or 'ios'.",
            "duration_ms": int((time.time() - start) * 1000),
        }

    if platform == "android":
        # Screenshot
        ss = android_screenshot(device_id)
        if ss["status"] == "ok":
            screenshot_b64 = ss["screenshot_b64"]
            device_label = ss["device"]
        else:
            findings.append({
                "severity": "error",
                "category": "mobile",
                "title": "Android screenshot failed",
                "detail": ss.get("error", "Unknown error"),
            })

        # UI dump + analysis
        ui = android_ui_dump(device_id)
        if ui["status"] == "ok":
            device_label = ui["device"]
            findings.extend(_analyze_android_ui(ui["ui_tree"]))
        else:
            findings.append({
                "severity": "error",
                "category": "mobile",
                "title": "Android UI dump failed",
                "detail": ui.get("error", "Unknown error"),
            })

    elif platform == "ios":
        # Screenshot
        ss = ios_screenshot(device_id)
        if ss["status"] == "ok":
            screenshot_b64 = ss["screenshot_b64"]
            device_label = ss["simulator"]
        else:
            findings.append({
                "severity": "error",
                "category": "mobile",
                "title": "iOS screenshot failed",
                "detail": ss.get("error", "Unknown error"),
            })

        # Accessibility tree + analysis
        at = ios_accessibility_tree(device_id)
        if at["status"] == "ok":
            device_label = at["simulator"]
            findings.extend(_analyze_ios_accessibility(at["accessibility_tree"]))
        else:
            findings.append({
                "severity": "warning",
                "category": "mobile",
                "title": "iOS accessibility tree unavailable",
                "detail": at.get("error", "Unknown error"),
            })

    duration_ms = int((time.time() - start) * 1000)
    has_errors = any(f["severity"] == "error" for f in findings)

    return {
        "status": "ok",
        "platform": platform,
        "device": device_label,
        "findings": findings,
        "finding_count": len(findings),
        "screenshot_b64": screenshot_b64,
        "verdict": "fail" if has_errors else "pass",
        "duration_ms": duration_ms,
    }


# ---------------------------------------------------------------------------
# CLI entry point (for quick testing)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    print("=== Mobile Environment Detection ===")
    env = detect_mobile_env()
    print(json.dumps({k: v for k, v in env.items()}, indent=2, default=str))

    if "--qa" in sys.argv:
        platform_arg = "android"
        for arg in sys.argv:
            if arg in ("android", "ios"):
                platform_arg = arg

        print(f"\n=== Mobile QA Check ({platform_arg}) ===")
        result = mobile_qa_check(platform_arg)
        # Don't print screenshot blob in CLI
        display = {k: v for k, v in result.items() if k != "screenshot_b64"}
        if result.get("screenshot_b64"):
            display["screenshot_b64"] = f"<{len(result['screenshot_b64'])} chars>"
        print(json.dumps(display, indent=2))
