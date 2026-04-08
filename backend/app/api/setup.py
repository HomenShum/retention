"""
Setup Status API

Checks local environment for device emulation requirements and provides
setup guidance for customers.
"""

import os
import subprocess
import platform
import shutil
from typing import Dict, Any, Optional, List
from fastapi import APIRouter, HTTPException
from datetime import datetime, timezone
import logging

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/setup", tags=["setup"])


def _run_command(cmd: List[str], timeout: int = 10) -> tuple[bool, str]:
    """Run a command and return (success, output)."""
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return result.returncode == 0, result.stdout.strip() or result.stderr.strip()
    except FileNotFoundError:
        return False, "Command not found"
    except subprocess.TimeoutExpired:
        return False, "Command timed out"
    except Exception as e:
        return False, str(e)


def _check_android_sdk() -> Dict[str, Any]:
    """Check Android SDK installation."""
    android_home = os.environ.get("ANDROID_HOME") or os.environ.get("ANDROID_SDK_ROOT")
    
    # Try common locations if not set
    if not android_home:
        home = os.path.expanduser("~")
        locations = [
            os.path.join(home, "Library", "Android", "sdk"),  # macOS
            os.path.join(home, "Android", "Sdk"),  # Linux
            os.path.join(os.environ.get("LOCALAPPDATA", ""), "Android", "Sdk"),  # Windows
        ]
        for loc in locations:
            if os.path.exists(loc):
                android_home = loc
                break
    
    if android_home and os.path.exists(android_home):
        return {
            "installed": True,
            "path": android_home,
            "platform_tools": os.path.exists(os.path.join(android_home, "platform-tools")),
            "emulator": os.path.exists(os.path.join(android_home, "emulator")),
        }
    return {"installed": False, "path": None}


def _check_adb() -> Dict[str, Any]:
    """Check ADB installation and devices."""
    success, output = _run_command(["adb", "version"])
    if success:
        # Get connected devices
        dev_success, dev_output = _run_command(["adb", "devices"])
        devices = []
        if dev_success:
            for line in dev_output.split("\n")[1:]:
                if line.strip() and "device" in line:
                    devices.append(line.split()[0])
        return {"installed": True, "version": output.split("\n")[0], "devices": devices}
    return {"installed": False, "version": None, "devices": []}


def _check_emulators() -> Dict[str, Any]:
    """Check available AVDs (Android Virtual Devices)."""
    android_home = os.environ.get("ANDROID_HOME", os.path.expanduser("~/Library/Android/sdk"))
    emulator_path = os.path.join(android_home, "emulator", "emulator")
    
    if os.path.exists(emulator_path):
        success, output = _run_command([emulator_path, "-list-avds"])
        avds = [line.strip() for line in output.split("\n") if line.strip()] if success else []
        return {"available": True, "avds": avds, "count": len(avds)}
    return {"available": False, "avds": [], "count": 0}


def _check_node() -> Dict[str, Any]:
    """Check Node.js installation (needed for Mobile MCP)."""
    success, output = _run_command(["node", "--version"])
    if success:
        return {"installed": True, "version": output}
    return {"installed": False, "version": None}


def _check_java() -> Dict[str, Any]:
    """Check Java installation (needed for Android SDK)."""
    success, output = _run_command(["java", "-version"])
    if success or "version" in output.lower():
        return {"installed": True, "version": output.split("\n")[0]}
    return {"installed": False, "version": None}


@router.get("/status")
async def get_setup_status():
    """Get comprehensive setup status for device emulation."""
    system = platform.system()
    
    android_sdk = _check_android_sdk()
    adb = _check_adb()
    emulators = _check_emulators()
    node = _check_node()
    java = _check_java()
    
    # Calculate overall readiness
    requirements_met = [
        android_sdk["installed"],
        adb["installed"],
        emulators["count"] > 0,
        node["installed"],
        java["installed"],
    ]
    ready = all(requirements_met)
    progress = sum(requirements_met) / len(requirements_met) * 100
    
    return {
        "ready": ready,
        "progress": round(progress),
        "system": {
            "os": system,
            "platform": platform.platform(),
            "arch": platform.machine(),
        },
        "requirements": {
            "android_sdk": android_sdk,
            "adb": adb,
            "emulators": emulators,
            "node": node,
            "java": java,
        },
        "timestamp": datetime.now(timezone.utc).isoformat()
    }


@router.get("/instructions")
async def get_setup_instructions():
    """Get platform-specific setup instructions."""
    system = platform.system()

    if system == "Darwin":  # macOS
        return _get_macos_instructions()
    elif system == "Linux":
        return _get_linux_instructions()
    elif system == "Windows":
        return _get_windows_instructions()
    else:
        return {"error": f"Unsupported platform: {system}"}


def _get_macos_instructions():
    return {
        "platform": "macOS",
        "quick_start": "curl -sSL https://raw.githubusercontent.com/HomenShum/retention/main/scripts/setup-macos.sh | bash",
        "steps": [
            {
                "name": "Install Homebrew",
                "check": "brew --version",
                "command": '/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"',
                "docs": "https://brew.sh"
            },
            {
                "name": "Install Java (OpenJDK)",
                "check": "java -version",
                "command": "brew install openjdk@17",
                "docs": "https://formulae.brew.sh/formula/openjdk"
            },
            {
                "name": "Install Android Studio",
                "check": "ls ~/Library/Android/sdk",
                "command": "brew install --cask android-studio",
                "docs": "https://developer.android.com/studio",
                "note": "Open Android Studio once to complete SDK setup"
            },
            {
                "name": "Install Android SDK Command-Line Tools",
                "check": "adb --version",
                "command": "sdkmanager --install 'platform-tools' 'emulator' 'platforms;android-34' 'system-images;android-34;google_apis;arm64-v8a'",
                "docs": "https://developer.android.com/tools/sdkmanager"
            },
            {
                "name": "Create an AVD (Virtual Device)",
                "check": "emulator -list-avds",
                "command": "avdmanager create avd -n Pixel_7_API_34 -k 'system-images;android-34;google_apis;arm64-v8a' -d pixel_7",
                "docs": "https://developer.android.com/studio/run/managing-avds"
            },
            {
                "name": "Set Environment Variables",
                "command": 'echo \'export ANDROID_HOME=$HOME/Library/Android/sdk\nexport PATH=$PATH:$ANDROID_HOME/emulator:$ANDROID_HOME/platform-tools\' >> ~/.zshrc && source ~/.zshrc'
            },
            {
                "name": "Install Node.js",
                "check": "node --version",
                "command": "brew install node",
                "docs": "https://nodejs.org"
            },
        ],
        "test_command": "adb devices && emulator -list-avds",
        "launch_emulator": "emulator -avd Pixel_7_API_34 -no-audio &"
    }


def _get_linux_instructions():
    return {
        "platform": "Linux",
        "quick_start": "curl -sSL https://raw.githubusercontent.com/HomenShum/retention/main/scripts/setup-linux.sh | bash",
        "steps": [
            {
                "name": "Install Java (OpenJDK)",
                "command": "sudo apt update && sudo apt install -y openjdk-17-jdk"
            },
            {
                "name": "Install Android SDK Command-Line Tools",
                "command": """
mkdir -p ~/Android/Sdk/cmdline-tools
cd ~/Android/Sdk/cmdline-tools
wget https://dl.google.com/android/repository/commandlinetools-linux-9477386_latest.zip
unzip commandlinetools-linux-*.zip
mv cmdline-tools latest
""".strip()
            },
            {
                "name": "Set Environment Variables",
                "command": 'echo \'export ANDROID_HOME=$HOME/Android/Sdk\nexport PATH=$PATH:$ANDROID_HOME/cmdline-tools/latest/bin:$ANDROID_HOME/platform-tools:$ANDROID_HOME/emulator\' >> ~/.bashrc && source ~/.bashrc'
            },
            {
                "name": "Install SDK Components",
                "command": "sdkmanager --install 'platform-tools' 'emulator' 'platforms;android-34' 'system-images;android-34;google_apis;x86_64'"
            },
            {
                "name": "Create an AVD",
                "command": "avdmanager create avd -n Pixel_7_API_34 -k 'system-images;android-34;google_apis;x86_64' -d pixel_7"
            },
            {
                "name": "Install Node.js",
                "command": "curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash - && sudo apt install -y nodejs"
            },
            {
                "name": "Enable KVM (for better emulator performance)",
                "command": "sudo apt install -y qemu-kvm && sudo adduser $USER kvm"
            }
        ],
        "test_command": "adb devices && emulator -list-avds",
        "launch_emulator": "emulator -avd Pixel_7_API_34 -no-audio &"
    }


def _get_windows_instructions():
    return {
        "platform": "Windows",
        "quick_start": "powershell -ExecutionPolicy Bypass -File setup-windows.ps1",
        "steps": [
            {
                "name": "Install Chocolatey",
                "command": "Set-ExecutionPolicy Bypass -Scope Process -Force; [System.Net.ServicePointManager]::SecurityProtocol = [System.Net.ServicePointManager]::SecurityProtocol -bor 3072; iex ((New-Object System.Net.WebClient).DownloadString('https://community.chocolatey.org/install.ps1'))",
                "docs": "https://chocolatey.org/install"
            },
            {
                "name": "Install Java",
                "command": "choco install openjdk17 -y"
            },
            {
                "name": "Install Android Studio",
                "command": "choco install androidstudio -y",
                "note": "Open Android Studio once to complete SDK setup"
            },
            {
                "name": "Set Environment Variables",
                "command": "[Environment]::SetEnvironmentVariable('ANDROID_HOME', \"$env:LOCALAPPDATA\\Android\\Sdk\", 'User')"
            },
            {
                "name": "Install Node.js",
                "command": "choco install nodejs -y"
            }
        ],
        "test_command": "adb devices; emulator -list-avds",
        "launch_emulator": "Start-Process emulator -ArgumentList '-avd Pixel_7_API_34 -no-audio'"
    }


@router.post("/launch-emulator")
async def launch_emulator(avd_name: Optional[str] = None):
    """Launch an Android emulator."""
    android_home = os.environ.get("ANDROID_HOME", os.path.expanduser("~/Library/Android/sdk"))
    emulator_path = os.path.join(android_home, "emulator", "emulator")

    if not os.path.exists(emulator_path):
        raise HTTPException(status_code=404, detail="Emulator not found. Please install Android SDK.")

    # Get available AVDs if none specified
    if not avd_name:
        success, output = _run_command([emulator_path, "-list-avds"])
        avds = [l.strip() for l in output.split("\n") if l.strip()]
        if not avds:
            raise HTTPException(status_code=404, detail="No AVDs found. Create one first.")
        avd_name = avds[0]

    # Launch emulator in background
    try:
        subprocess.Popen(
            [emulator_path, "-avd", avd_name, "-no-audio", "-gpu", "swiftshader_indirect"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True
        )
        return {"success": True, "avd": avd_name, "message": f"Launching {avd_name}..."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

