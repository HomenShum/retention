"""
retention.sh — Emulator frame streaming.

Captures emulator screen frames at a configurable interval and sends them
to the retention.sh server through the WebSocket connection. Used for live
preview and ActionSpan clip recording.

Frames are captured via ADB screencap and sent as base64 PNG. The server
decides when to start/stop streaming based on test execution needs.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import os
import shutil
from typing import Any, Optional

logger = logging.getLogger(__name__)

DEFAULT_FPS = 2  # frames per second — enough for ActionSpan verification
MAX_FPS = 10


def _adb_path() -> str:
    """Resolve ADB binary path."""
    android_home = os.environ.get(
        "ANDROID_HOME",
        os.path.expanduser("~/Library/Android/sdk"),
    )
    adb = os.path.join(android_home, "platform-tools", "adb")
    if os.path.isfile(adb):
        return adb
    found = shutil.which("adb")
    return found or "adb"


class FrameStreamer:
    """Captures and streams emulator frames to the server."""

    def __init__(self, send_fn: Any, device: Optional[str] = None, fps: int = DEFAULT_FPS):
        """
        Args:
            send_fn: async callable that sends a dict message over WebSocket
            device: ADB device serial (None = default device)
            fps: target frames per second
        """
        self._send = send_fn
        self._device = device
        self._fps = min(fps, MAX_FPS)
        self._task: Optional[asyncio.Task[None]] = None
        self._session_id: Optional[str] = None

    async def start(self, session_id: str) -> None:
        """Start streaming frames for a given session."""
        if self._task and not self._task.done():
            await self.stop()
        self._session_id = session_id
        self._task = asyncio.create_task(self._stream_loop())
        logger.info("Frame streaming started (session=%s, fps=%d)", session_id, self._fps)

    async def stop(self) -> None:
        """Stop streaming."""
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("Frame streaming stopped (session=%s)", self._session_id)
        self._session_id = None

    async def capture_single(self) -> Optional[str]:
        """Capture a single frame and return base64 PNG."""
        cmd = [_adb_path()]
        if self._device:
            cmd += ["-s", self._device]
        cmd += ["exec-out", "screencap", "-p"]

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()
            if proc.returncode != 0:
                logger.warning("screencap failed: %s", stderr.decode(errors="replace"))
                return None
            return base64.b64encode(stdout).decode()
        except Exception:
            logger.exception("Frame capture error")
            return None

    async def _stream_loop(self) -> None:
        """Continuously capture and send frames."""
        interval = 1.0 / self._fps
        while True:
            frame_b64 = await self.capture_single()
            if frame_b64:
                await self._send({
                    "type": "frame",
                    "session_id": self._session_id,
                    "image_base64": frame_b64,
                    "format": "png",
                })
            await asyncio.sleep(interval)
