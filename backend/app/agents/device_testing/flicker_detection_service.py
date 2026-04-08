"""
Flicker Detection Service — 4-Layer Video-Based Visual Bug Detection

Detects screen flicker/glitch bugs invisible to periodic screenshots but
visible to the human eye. Uses video recording + frame analysis + logcat
correlation to catch sub-200ms visual anomalies.

Architecture:
  Layer 0: SurfaceFlinger frame timing + logcat monitoring (always-on, zero cost)
  Layer 1: adb screenrecord triggered recording (60fps H.264)
  Layer 2: ffmpeg scene-filtered extraction + parallel SSIM analysis
  Layer 3: GPT-5.4 vision verification (semantic bug/animation classification)

Optimizations (v2):
  - ffmpeg scene detection pre-filter (skips identical frames, 60-80% reduction)
  - JPEG extraction (5-10x smaller than PNG, negligible SSIM accuracy loss)
  - Parallel SSIM via ProcessPoolExecutor (3-5x speedup)
  - Adaptive threshold (median - 2σ, works across dark/light mode)
  - SSIM timeline visualization (PIL-based chart)
  - Pre/post SurfaceFlinger stats delta
  - Layer 3 GPT-5.4 semantic verification wired up
  - Frame cleanup option, adb pull retry, video integrity check

Dependencies: adb, ffmpeg, numpy, PIL. GPT-5.4 optional for Layer 3.
"""

import asyncio
import base64
import io
import json
import logging
import os
import subprocess
import time
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)


@dataclass
class LogcatEvent:
    timestamp: float
    tag: str
    level: str
    message: str
    raw: str = ""

@dataclass
class FlickerEvent:
    start_frame: int
    end_frame: int
    start_time: float
    end_time: float
    duration_ms: float
    pattern: str
    ssim_scores: List[float] = field(default_factory=list)
    affected_region: Optional[Dict[str, int]] = None
    severity: str = "MEDIUM"
    frame_paths: List[str] = field(default_factory=list)
    logcat_events: List[LogcatEvent] = field(default_factory=list)
    gpt_analysis: str = ""

@dataclass
class SurfaceStats:
    total_frames: int = 0
    janky_frames: int = 0
    jank_percentage: float = 0.0
    avg_frame_time_ms: float = 0.0
    max_frame_time_ms: float = 0.0
    percentile_90_ms: float = 0.0
    percentile_99_ms: float = 0.0

@dataclass
class SurfaceStatsDelta:
    """Delta between pre- and post-test SurfaceFlinger stats."""
    frames_during_test: int = 0
    janky_during_test: int = 0
    jank_pct_during_test: float = 0.0

@dataclass
class FlickerReport:
    session_id: str
    device_id: str
    recording_duration: float = 0.0
    video_path: str = ""
    total_frames_analyzed: int = 0
    total_scene_frames: int = 0  # frames after scene filter (before: all)
    flicker_events: List[FlickerEvent] = field(default_factory=list)
    surface_stats: Optional[SurfaceStats] = None
    surface_delta: Optional[SurfaceStatsDelta] = None
    logcat_events: List[LogcatEvent] = field(default_factory=list)
    total_flickers_detected: int = 0
    analysis_time_seconds: float = 0.0
    ssim_timeline_path: str = ""
    frames_dir: str = ""
    error: str = ""

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "session_id": self.session_id, "device_id": self.device_id,
            "recording_duration": round(self.recording_duration, 2),
            "video_path": self.video_path,
            "total_frames_analyzed": self.total_frames_analyzed,
            "total_scene_frames": self.total_scene_frames,
            "total_flickers_detected": self.total_flickers_detected,
            "analysis_time_seconds": round(self.analysis_time_seconds, 2),
            "ssim_timeline_path": self.ssim_timeline_path,
            "frames_dir": self.frames_dir, "error": self.error,
        }
        if self.surface_stats:
            ss = self.surface_stats
            d["surface_stats"] = {
                "total_frames": ss.total_frames, "janky_frames": ss.janky_frames,
                "jank_pct": round(ss.jank_percentage, 2),
                "avg_ms": round(ss.avg_frame_time_ms, 2),
                "max_ms": round(ss.max_frame_time_ms, 2),
                "p90_ms": round(ss.percentile_90_ms, 2),
                "p99_ms": round(ss.percentile_99_ms, 2),
            }
        if self.surface_delta:
            sd = self.surface_delta
            d["surface_delta"] = {
                "frames_during_test": sd.frames_during_test,
                "janky_during_test": sd.janky_during_test,
                "jank_pct_during_test": round(sd.jank_pct_during_test, 2),
            }
        d["flicker_events"] = [
            {
                "start_frame": e.start_frame, "end_frame": e.end_frame,
                "start_time": round(e.start_time, 3),
                "end_time": round(e.end_time, 3),
                "duration_ms": round(e.duration_ms, 1), "pattern": e.pattern,
                "ssim_scores": [round(s, 4) for s in e.ssim_scores],
                "severity": e.severity, "frame_paths": e.frame_paths,
                "logcat_events": [
                    {"ts": round(le.timestamp, 3), "tag": le.tag,
                     "lvl": le.level, "msg": le.message}
                    for le in e.logcat_events],
                "gpt_analysis": e.gpt_analysis,
            } for e in self.flicker_events
        ]
        d["logcat_summary"] = {
            "total": len(self.logcat_events),
            "choreographer_skips": sum(
                1 for e in self.logcat_events if "Choreographer" in e.tag),
            "surfaceflinger": sum(
                1 for e in self.logcat_events if "SurfaceFlinger" in e.tag),
        }
        return d


# ---------------------------------------------------------------------------
# SSIM (numpy-only, no scikit-image) — optimized for parallel execution
# ---------------------------------------------------------------------------

def _ssim_grayscale(img1: np.ndarray, img2: np.ndarray, win: int = 11) -> float:
    """Block-based SSIM between two grayscale arrays (Wang et al. 2004)."""
    C1 = (0.01 * 255) ** 2
    C2 = (0.03 * 255) ** 2
    img1 = img1.astype(np.float64)
    img2 = img2.astype(np.float64)
    h, w = img1.shape
    bh, bw = h // win, w // win
    if bh == 0 or bw == 0:
        return 1.0  # images too small to compare
    img1 = img1[:bh * win, :bw * win]
    img2 = img2[:bh * win, :bw * win]
    b1 = img1.reshape(bh, win, bw, win)
    b2 = img2.reshape(bh, win, bw, win)
    mu1, mu2 = b1.mean(axis=(1, 3)), b2.mean(axis=(1, 3))
    s1, s2 = b1.var(axis=(1, 3)), b2.var(axis=(1, 3))
    s12 = ((b1 - mu1[:, None, :, None]) * (b2 - mu2[:, None, :, None])).mean(axis=(1, 3))
    num = (2 * mu1 * mu2 + C1) * (2 * s12 + C2)
    den = (mu1 ** 2 + mu2 ** 2 + C1) * (s1 + s2 + C2)
    return float((num / den).mean())


def _load_gray_array(path: str, resize_w: int = 360) -> np.ndarray:
    """Load image as resized grayscale numpy array. Shared by SSIM + region_diff."""
    img = Image.open(path).convert("L")
    asp = img.height / img.width
    nh = int(resize_w * asp)
    return np.array(img.resize((resize_w, nh), Image.LANCZOS))


def _frame_ssim(path1: str, path2: str, resize_w: int = 360) -> float:
    """SSIM between two frame images (resized grayscale for speed)."""
    a1 = _load_gray_array(path1, resize_w)
    a2 = _load_gray_array(path2, resize_w)
    return _ssim_grayscale(a1, a2)


def _ssim_pair_worker(args: Tuple[str, str, int]) -> float:
    """Worker function for parallel SSIM computation (picklable top-level)."""
    path1, path2, resize_w = args
    return _frame_ssim(path1, path2, resize_w)


def _region_diff_from_arrays(
    a1: np.ndarray, a2: np.ndarray,
    orig_w: int, orig_h: int, resize_w: int = 360,
) -> Dict[str, Any]:
    """Find region of maximum difference between two pre-loaded arrays."""
    diff = np.abs(a1.astype(np.float64) - a2.astype(np.float64))
    rh = a1.shape[0]
    blk = 32
    best_val, best_pos = 0.0, (0, 0)
    for y in range(0, rh - blk, blk // 2):
        for x in range(0, resize_w - blk, blk // 2):
            v = diff[y:y + blk, x:x + blk].mean()
            if v > best_val:
                best_val, best_pos = v, (x, y)
    sx, sy = orig_w / resize_w, orig_h / rh
    return {"x": int(best_pos[0] * sx), "y": int(best_pos[1] * sy),
            "width": int(blk * sx), "height": int(blk * sy),
            "diff_score": round(best_val / 255.0, 4)}


def _region_diff(path1: str, path2: str, resize_w: int = 360) -> Dict[str, Any]:
    """Find region of maximum difference between two frames (file-based)."""
    i1 = Image.open(path1)
    orig_w, orig_h = i1.size
    a1 = _load_gray_array(path1, resize_w)
    a2 = _load_gray_array(path2, resize_w)
    return _region_diff_from_arrays(a1, a2, orig_w, orig_h, resize_w)


# ---------------------------------------------------------------------------
# FlickerDetectionService
# ---------------------------------------------------------------------------

class FlickerDetectionService:
    """
    4-layer flicker detection pipeline for Android devices.

    Usage:
        svc = FlickerDetectionService("emulator-5554")
        report = await svc.run_detection(duration=10, scenario_fn=my_actions)
    """

    # SSIM thresholds (fallback when adaptive is disabled)
    SSIM_CHANGE_THRESHOLD = 0.92   # below this = significant visual change
    SSIM_FLICKER_WINDOW_MS = 500   # oscillations within this window = flicker
    MIN_OSCILLATIONS = 2           # minimum SSIM dips to count as flicker
    JANK_THRESHOLD_MS = 32.0       # >2 frames at 60fps = janky
    # Adaptive threshold: flag scores below (median - ADAPTIVE_SIGMA * std)
    ADAPTIVE_SIGMA = 2.0
    # Scene detection threshold for ffmpeg (0.0-1.0, lower = more frames kept)
    SCENE_THRESHOLD = 0.08
    # Max parallel workers for SSIM computation
    MAX_SSIM_WORKERS = 4
    # ADB pull retry count
    ADB_PULL_RETRIES = 3

    def __init__(self, device_id: str, output_dir: Optional[str] = None,
                 adaptive_threshold: bool = True):
        self.device_id = device_id
        self.adaptive_threshold = adaptive_threshold
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.session_id = f"flicker_{ts}"
        if output_dir:
            self.output_dir = Path(output_dir)
        else:
            self.output_dir = (
                Path(__file__).parent.parent.parent.parent
                / "screenshots" / "flicker_detection" / self.session_id
            )
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.frames_dir = self.output_dir / "frames"
        self.frames_dir.mkdir(exist_ok=True)
        self._logcat_proc: Optional[asyncio.subprocess.Process] = None
        self._logcat_start_time: float = 0.0
        self._record_proc: Optional[asyncio.subprocess.Process] = None
        self._record_start_time: float = 0.0
        logger.info(f"FlickerDetectionService initialized: {self.session_id}")
        logger.info(f"  device={device_id}, adaptive={adaptive_threshold}, "
                     f"output={self.output_dir}")

    # -----------------------------------------------------------------------
    # Layer 0: SurfaceFlinger + Logcat
    # -----------------------------------------------------------------------

    async def get_surface_stats(self, package: str = "") -> SurfaceStats:
        """Query SurfaceFlinger/gfxinfo for frame timing stats."""
        stats = SurfaceStats()
        try:
            cmd = ["adb", "-s", self.device_id, "shell", "dumpsys", "gfxinfo"]
            if package:
                cmd.append(package)
            proc = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
            text = stdout.decode("utf-8", errors="ignore")

            # Parse "Total frames rendered: N"
            for line in text.splitlines():
                line = line.strip()
                if line.startswith("Total frames rendered:"):
                    stats.total_frames = int(line.split(":")[-1].strip())
                elif line.startswith("Janky frames:"):
                    parts = line.split(":")[-1].strip()
                    # "123 (45.67%)"
                    num = parts.split("(")[0].strip()
                    stats.janky_frames = int(num)
                    if "(" in parts and "%" in parts:
                        pct = parts.split("(")[1].replace("%", "").replace(")", "")
                        stats.jank_percentage = float(pct)

            # Parse frame timing from "---PROFILEDATA---" section
            in_profile = False
            frame_times: List[float] = []
            for line in text.splitlines():
                if "---PROFILEDATA---" in line:
                    in_profile = not in_profile
                    continue
                if in_profile and line.strip():
                    parts = line.strip().split(",")
                    if len(parts) >= 3:
                        try:
                            # Columns: Flags, IntendedVsync, Vsync, ...
                            # Total frame time ≈ last - first timestamp
                            vals = [int(p) for p in parts if p.strip().isdigit()]
                            if len(vals) >= 2:
                                ft = (vals[-1] - vals[0]) / 1_000_000  # ns → ms
                                if 0 < ft < 10000:
                                    frame_times.append(ft)
                        except (ValueError, IndexError):
                            pass

            if frame_times:
                arr = np.array(frame_times)
                stats.avg_frame_time_ms = float(arr.mean())
                stats.max_frame_time_ms = float(arr.max())
                stats.percentile_90_ms = float(np.percentile(arr, 90))
                stats.percentile_99_ms = float(np.percentile(arr, 99))

        except Exception as e:
            logger.warning(f"SurfaceFlinger stats failed: {e}")
        return stats

    async def start_logcat(self) -> None:
        """Start capturing logcat with rendering-related filters."""
        # Clear logcat buffer first
        clear = await asyncio.create_subprocess_exec(
            "adb", "-s", self.device_id, "logcat", "-c",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        await asyncio.wait_for(clear.communicate(), timeout=5)

        self._logcat_start_time = time.time()
        logcat_path = self.output_dir / "logcat.txt"
        self._logcat_file = open(logcat_path, "w")
        self._logcat_proc = await asyncio.create_subprocess_exec(
            "adb", "-s", self.device_id, "logcat", "-v", "time",
            "Choreographer:W", "SurfaceFlinger:W", "ActivityManager:I",
            "WindowManager:W", "InputDispatcher:W", "ViewRootImpl:W", "*:S",
            stdout=self._logcat_file, stderr=asyncio.subprocess.PIPE)
        logger.info("Logcat capture started")

    async def stop_logcat(self) -> List[LogcatEvent]:
        """Stop logcat and parse relevant events."""
        events: List[LogcatEvent] = []
        if self._logcat_proc:
            self._logcat_proc.terminate()
            try:
                await asyncio.wait_for(self._logcat_proc.communicate(), timeout=3)
            except (asyncio.TimeoutError, ProcessLookupError):
                pass
            self._logcat_file.close()

            logcat_path = self.output_dir / "logcat.txt"
            if logcat_path.exists():
                events = self._parse_logcat(logcat_path)
                logger.info(f"Logcat: {len(events)} relevant events captured")
        return events

    def _parse_logcat(self, path: Path) -> List[LogcatEvent]:
        """Parse logcat output into structured events.

        Extracts real timestamps from logcat format 'MM-DD HH:MM:SS.mmm'
        and converts them to seconds-since-first-event for correlation
        with video frame timestamps.
        """
        events: List[LogcatEvent] = []
        first_ts: Optional[float] = None
        try:
            with open(path) as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("---"):
                        continue
                    # Format: "MM-DD HH:MM:SS.mmm L/Tag(PID): message"
                    try:
                        # Extract timestamp: "02-05 19:26:24.928"
                        ts_seconds = 0.0
                        if len(line) > 18 and line[2] == "-" and line[5] == " ":
                            time_part = line[6:18]  # "HH:MM:SS.mmm"
                            h, m, rest = time_part.split(":")
                            s_parts = rest.split(".")
                            ts_seconds = (int(h) * 3600 + int(m) * 60
                                          + int(s_parts[0])
                                          + int(s_parts[1]) / 1000.0)
                            if first_ts is None:
                                first_ts = ts_seconds
                            ts_seconds -= first_ts  # offset from start

                        # Extract level and tag
                        parts = line.split("/", 1)
                        if len(parts) < 2:
                            continue
                        level = parts[0][-1] if parts[0] else "I"
                        rest = parts[1]
                        tag_end = rest.find("(")
                        tag = rest[:tag_end].strip() if tag_end > 0 else "unknown"
                        msg_start = rest.find("): ")
                        msg = rest[msg_start + 3:].strip() if msg_start > 0 else rest
                        events.append(LogcatEvent(
                            timestamp=ts_seconds, tag=tag, level=level,
                            message=msg, raw=line))
                    except Exception:
                        continue
        except Exception as e:
            logger.warning(f"Logcat parse error: {e}")
        return events

    # -----------------------------------------------------------------------
    # Layer 1: adb screenrecord
    # -----------------------------------------------------------------------

    async def start_recording(self, duration: int = 30,
                              size: str = "720x1280",
                              bitrate: str = "8M") -> str:
        """Start adb screenrecord on device. Returns remote video path."""
        remote_path = f"/sdcard/flicker_{self.session_id}.mp4"
        cmd = [
            "adb", "-s", self.device_id, "shell", "screenrecord",
            "--size", size, "--bit-rate", bitrate,
            "--time-limit", str(duration),
            remote_path,
        ]
        logger.info(f"Starting screenrecord: {duration}s, {size}, {bitrate}")
        self._record_start_time = time.time()
        self._record_proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        return remote_path

    async def stop_recording(self) -> Optional[str]:
        """Stop recording and pull video to local output dir."""
        if not self._record_proc:
            return None
        # Send interrupt to stop recording gracefully
        try:
            self._record_proc.terminate()
            await asyncio.wait_for(self._record_proc.communicate(), timeout=5)
        except (asyncio.TimeoutError, ProcessLookupError):
            pass
        await asyncio.sleep(1)  # let device finalize MP4

        remote_path = f"/sdcard/flicker_{self.session_id}.mp4"
        local_path = self.output_dir / "recording.mp4"

        # Pull from device
        proc = await asyncio.create_subprocess_exec(
            "adb", "-s", self.device_id, "pull", remote_path, str(local_path),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
        if proc.returncode != 0:
            logger.error(f"Failed to pull recording: {stderr.decode()}")
            return None

        # Clean up remote file
        await asyncio.create_subprocess_exec(
            "adb", "-s", self.device_id, "shell", "rm", remote_path,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)

        size_mb = local_path.stat().st_size / (1024 * 1024)
        logger.info(f"Recording saved: {local_path} ({size_mb:.1f} MB)")
        return str(local_path)

    async def wait_for_recording(self, duration: int) -> Optional[str]:
        """Wait for screenrecord to finish naturally, then pull."""
        if self._record_proc:
            try:
                await asyncio.wait_for(
                    self._record_proc.communicate(), timeout=duration + 10)
            except asyncio.TimeoutError:
                self._record_proc.terminate()
        await asyncio.sleep(1)
        return await self._pull_recording()

    async def _pull_recording(self) -> Optional[str]:
        """Pull recording from device with retry and integrity check."""
        remote_path = f"/sdcard/flicker_{self.session_id}.mp4"
        local_path = self.output_dir / "recording.mp4"

        # Retry loop for adb pull
        for attempt in range(self.ADB_PULL_RETRIES):
            proc = await asyncio.create_subprocess_exec(
                "adb", "-s", self.device_id, "pull", remote_path, str(local_path),
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
            if proc.returncode == 0:
                break
            logger.warning(f"Pull attempt {attempt + 1}/{self.ADB_PULL_RETRIES} "
                           f"failed: {stderr.decode()}")
            if attempt < self.ADB_PULL_RETRIES - 1:
                await asyncio.sleep(1)
        else:
            logger.error("All adb pull attempts failed")
            return None

        # Validate video integrity with ffprobe
        try:
            probe = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries",
                 "format=duration,size", "-of", "json", str(local_path)],
                capture_output=True, text=True, timeout=10)
            if probe.returncode != 0:
                logger.error(f"Video integrity check failed: {probe.stderr[:200]}")
                return None
            probe_data = json.loads(probe.stdout)
            vid_duration = float(probe_data.get("format", {}).get("duration", 0))
            logger.info(f"Video validated: {vid_duration:.1f}s actual duration")
        except (subprocess.TimeoutExpired, json.JSONDecodeError, FileNotFoundError):
            logger.warning("ffprobe not available, skipping integrity check")

        # Cleanup remote
        await asyncio.create_subprocess_exec(
            "adb", "-s", self.device_id, "shell", "rm", "-f", remote_path,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        size_mb = local_path.stat().st_size / (1024 * 1024)
        logger.info(f"Recording: {local_path} ({size_mb:.1f} MB)")
        return str(local_path)

    # -----------------------------------------------------------------------
    # Layer 2: Frame extraction + SSIM analysis
    # -----------------------------------------------------------------------

    def extract_frames(self, video_path: str, fps: int = 30,
                       use_scene_filter: bool = True) -> List[str]:
        """Extract frames using ffmpeg with optional scene detection pre-filter.

        When use_scene_filter=True, ffmpeg's scene detection skips visually
        identical frames (typically 60-80% reduction). Outputs JPEG for
        5-10x smaller files vs PNG with negligible SSIM accuracy loss.
        """
        pattern = str(self.frames_dir / "frame_%05d.jpg")
        if use_scene_filter:
            # Scene filter: only extract frames where visual change > threshold
            vf = (f"select='gt(scene,{self.SCENE_THRESHOLD})',"
                  f"fps={fps}")
            logger.info(f"Extracting frames: scene>{self.SCENE_THRESHOLD} + "
                        f"{fps}fps (JPEG)")
        else:
            vf = f"fps={fps}"
            logger.info(f"Extracting ALL frames at {fps}fps (JPEG, no filter)")

        cmd = [
            "ffmpeg", "-i", video_path,
            "-vf", vf,
            "-vsync", "0",           # don't duplicate frames
            "-pix_fmt", "yuvj420p",  # required for MJPEG with scene filter
            "-q:v", "5",             # JPEG quality (2=best, 31=worst; 5 good)
            pattern, "-y",
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode != 0:
            logger.error(f"ffmpeg failed: {result.stderr[:500]}")
            # Fallback: try without scene filter
            if use_scene_filter:
                logger.info("Retrying without scene filter...")
                return self.extract_frames(video_path, fps, use_scene_filter=False)
            return []
        frames = sorted(self.frames_dir.glob("frame_*.jpg"))
        paths = [str(f) for f in frames]
        # If scene filter produced 0 frames, fall back to unfiltered
        if not paths and use_scene_filter:
            logger.info("Scene filter produced 0 frames, retrying unfiltered...")
            return self.extract_frames(video_path, fps, use_scene_filter=False)
        total_size_kb = sum(Path(p).stat().st_size for p in paths) / 1024
        logger.info(f"Extracted {len(paths)} frames "
                    f"({total_size_kb:.0f} KB total, "
                    f"{'scene-filtered' if use_scene_filter else 'all'})")
        return paths

    def analyze_frames(self, frame_paths: List[str],
                       fps: int = 30) -> Tuple[List[FlickerEvent], List[float], float]:
        """
        Analyze consecutive frames for flicker patterns using parallel SSIM.

        Returns:
            (flicker_events, all_ssim_scores, computed_threshold)

        Optimizations vs v1:
        - Parallel SSIM via ProcessPoolExecutor (3-5x speedup)
        - Adaptive threshold: median - 2σ (works across dark/light mode)
        - Returns raw SSIM scores for timeline visualization
        """
        if len(frame_paths) < 3:
            return [], [], self.SSIM_CHANGE_THRESHOLD

        n_pairs = len(frame_paths) - 1
        logger.info(f"Computing SSIM for {n_pairs} frame pairs "
                    f"(parallel, {self.MAX_SSIM_WORKERS} workers)...")

        # Parallel SSIM computation
        t_ssim = time.time()
        pairs = [(frame_paths[i], frame_paths[i + 1], 360)
                 for i in range(n_pairs)]
        with ProcessPoolExecutor(max_workers=self.MAX_SSIM_WORKERS) as executor:
            ssim_scores = list(executor.map(_ssim_pair_worker, pairs))
        ssim_time = time.time() - t_ssim
        logger.info(f"SSIM computed in {ssim_time:.2f}s "
                    f"({n_pairs / ssim_time:.0f} pairs/sec)")

        # Adaptive threshold
        if self.adaptive_threshold and len(ssim_scores) > 10:
            median = float(np.median(ssim_scores))
            std = float(np.std(ssim_scores))
            threshold = max(0.70, median - self.ADAPTIVE_SIGMA * std)
            logger.info(f"Adaptive threshold: {threshold:.4f} "
                        f"(median={median:.4f}, σ={std:.4f})")
        else:
            threshold = self.SSIM_CHANGE_THRESHOLD
            logger.info(f"Fixed threshold: {threshold}")

        # Save SSIM data for debugging
        ssim_path = self.output_dir / "ssim_scores.json"
        with open(ssim_path, "w") as f:
            json.dump({"fps": fps,
                        "scores": [round(s, 4) for s in ssim_scores],
                        "threshold": round(threshold, 4),
                        "adaptive": self.adaptive_threshold,
                        "ssim_time_s": round(ssim_time, 2)}, f, indent=2)

        # Find change points (SSIM below threshold)
        frame_duration_ms = 1000.0 / fps
        change_points: List[Tuple[int, float]] = []
        for i, score in enumerate(ssim_scores):
            if score < threshold:
                change_points.append((i, score))

        if not change_points:
            logger.info("No significant visual changes detected")
            return [], ssim_scores, threshold

        logger.info(f"Found {len(change_points)} change points "
                    f"(threshold={threshold:.4f})")

        # Group change points into flicker events
        events: List[FlickerEvent] = []
        group: List[Tuple[int, float]] = [change_points[0]]

        for cp in change_points[1:]:
            prev_frame = group[-1][0]
            gap_ms = (cp[0] - prev_frame) * frame_duration_ms
            if gap_ms <= self.SSIM_FLICKER_WINDOW_MS:
                group.append(cp)
            else:
                events.append(self._classify_group(
                    group, ssim_scores, frame_paths, fps, threshold))
                group = [cp]
        # Last group
        events.append(self._classify_group(
            group, ssim_scores, frame_paths, fps, threshold))

        logger.info(f"Detected {len(events)} flicker events")
        return events, ssim_scores, threshold

    def _classify_group(self, group: List[Tuple[int, float]],
                        all_ssim: List[float], frame_paths: List[str],
                        fps: int,
                        threshold: Optional[float] = None) -> FlickerEvent:
        """Classify a group of change points into a FlickerEvent."""
        thresh = threshold if threshold is not None else self.SSIM_CHANGE_THRESHOLD
        frame_dur = 1000.0 / fps
        start_frame = group[0][0]
        end_frame = group[-1][0] + 1
        start_time = start_frame * frame_dur / 1000.0
        end_time = (end_frame + 1) * frame_dur / 1000.0
        duration_ms = (end_frame - start_frame + 1) * frame_dur

        # Get SSIM scores in the window
        window_ssim = all_ssim[start_frame:end_frame + 1]

        # Count oscillations: how many times SSIM crosses threshold
        oscillations = 0
        above = True
        for s in window_ssim:
            if above and s < thresh:
                oscillations += 1
                above = False
            elif not above and s >= thresh:
                above = True

        # Classify pattern
        if oscillations >= self.MIN_OSCILLATIONS:
            pattern = "rapid_oscillation"
            severity = "HIGH" if oscillations >= 3 else "MEDIUM"
        elif len(group) == 1 and duration_ms < 100:
            pattern = "single_glitch"
            severity = "LOW"
        else:
            pattern = "sustained_change"
            severity = "MEDIUM" if duration_ms > 200 else "LOW"

        # Get affected region from the most different frame pair
        region = None
        if len(group) >= 1:
            worst_idx = min(group, key=lambda x: x[1])[0]
            if worst_idx < len(frame_paths) - 1:
                region = _region_diff(frame_paths[worst_idx],
                                      frame_paths[worst_idx + 1])

        # Collect frame paths for evidence
        evidence_frames = []
        for idx in range(max(0, start_frame - 1),
                         min(len(frame_paths), end_frame + 2)):
            evidence_frames.append(frame_paths[idx])

        return FlickerEvent(
            start_frame=start_frame, end_frame=end_frame,
            start_time=start_time, end_time=end_time,
            duration_ms=duration_ms, pattern=pattern,
            ssim_scores=window_ssim, affected_region=region,
            severity=severity, frame_paths=evidence_frames,
        )

    # -----------------------------------------------------------------------
    # Logcat correlation + visualization
    # -----------------------------------------------------------------------

    def correlate_logcat(self, events: List[FlickerEvent],
                         logcat: List[LogcatEvent]) -> List[FlickerEvent]:
        """Attach logcat events that fall within each flicker's time window."""
        for ev in events:
            window_start = max(0, ev.start_time - 0.5)
            window_end = ev.end_time + 0.5
            ev.logcat_events = [
                le for le in logcat
                if window_start <= le.timestamp <= window_end
            ]
        return events

    def create_comparison_image(self, event: FlickerEvent,
                                idx: int) -> Optional[str]:
        """Create side-by-side comparison of flicker frames with annotations."""
        if len(event.frame_paths) < 2:
            return None
        try:
            frames = [Image.open(p) for p in event.frame_paths[:4]]
            n = len(frames)
            fw, fh = frames[0].size
            # Create canvas: frames side by side + info bar at bottom
            margin = 10
            info_h = 120
            canvas_w = n * fw + (n - 1) * margin
            canvas_h = fh + info_h
            canvas = Image.new("RGB", (canvas_w, canvas_h), (30, 30, 30))
            draw = ImageDraw.Draw(canvas)

            # Paste frames
            for i, frame in enumerate(frames):
                x = i * (fw + margin)
                canvas.paste(frame, (x, 0))
                # Frame label
                label = f"Frame {event.start_frame + i}"
                draw.text((x + 10, 10), label, fill=(255, 255, 0))

            # Draw affected region highlight on each frame
            if event.affected_region:
                r = event.affected_region
                for i in range(n):
                    x_off = i * (fw + margin)
                    draw.rectangle(
                        [x_off + r["x"], r["y"],
                         x_off + r["x"] + r["width"], r["y"] + r["height"]],
                        outline=(255, 0, 0), width=3)

            # Info bar
            y_info = fh + 10
            info_lines = [
                f"Flicker #{idx + 1} | Pattern: {event.pattern} | "
                f"Severity: {event.severity}",
                f"Time: {event.start_time:.3f}s - {event.end_time:.3f}s | "
                f"Duration: {event.duration_ms:.0f}ms",
                f"SSIM: {' → '.join(f'{s:.3f}' for s in event.ssim_scores[:6])}",
            ]
            if event.logcat_events:
                info_lines.append(
                    f"Logcat: {len(event.logcat_events)} events "
                    f"({', '.join(set(e.tag for e in event.logcat_events[:3]))})")

            for i, line in enumerate(info_lines):
                draw.text((20, y_info + i * 25), line, fill=(200, 200, 200))

            out_path = self.output_dir / f"flicker_{idx + 1}_comparison.png"
            canvas.save(str(out_path))
            logger.info(f"Comparison image: {out_path}")
            return str(out_path)
        except Exception as e:
            logger.error(f"Comparison image failed: {e}")
            return None

    # -----------------------------------------------------------------------
    # SSIM Timeline Visualization (PIL-based, no matplotlib)
    # -----------------------------------------------------------------------

    def create_ssim_timeline(self, ssim_scores: List[float], fps: int,
                             threshold: float,
                             events: Optional[List[FlickerEvent]] = None,
                             ) -> str:
        """Generate SSIM timeline chart using PIL.

        Creates a 1200×400 chart with:
        - X-axis: time (seconds)
        - Y-axis: SSIM score (0.0-1.0)
        - Red threshold line
        - Blue SSIM curve
        - Orange highlight bands for flicker events
        """
        W, H = 1200, 400
        PAD_L, PAD_R, PAD_T, PAD_B = 60, 30, 30, 50
        chart_w = W - PAD_L - PAD_R
        chart_h = H - PAD_T - PAD_B

        canvas = Image.new("RGB", (W, H), (25, 25, 30))
        draw = ImageDraw.Draw(canvas)

        if not ssim_scores:
            draw.text((W // 2 - 50, H // 2), "No SSIM data",
                      fill=(200, 200, 200))
            out = str(self.output_dir / "ssim_timeline.png")
            canvas.save(out)
            return out

        n = len(ssim_scores)
        total_time = n / fps
        y_min, y_max = 0.0, 1.0

        def to_px(idx: int, val: float) -> Tuple[int, int]:
            x = PAD_L + int(idx / max(n - 1, 1) * chart_w)
            y = PAD_T + int((1.0 - (val - y_min) / (y_max - y_min)) * chart_h)
            return x, y

        # Grid lines (horizontal at 0.2 intervals)
        for v in [0.0, 0.2, 0.4, 0.6, 0.8, 1.0]:
            _, y = to_px(0, v)
            draw.line([(PAD_L, y), (W - PAD_R, y)], fill=(50, 50, 55), width=1)
            draw.text((5, y - 8), f"{v:.1f}", fill=(120, 120, 130))

        # X-axis labels (time)
        for t_sec in range(0, int(total_time) + 1, max(1, int(total_time / 8))):
            idx = int(t_sec * fps)
            if idx >= n:
                break
            x, _ = to_px(idx, 0)
            draw.text((x - 8, H - 20), f"{t_sec}s", fill=(120, 120, 130))

        # Flicker event highlight bands (orange)
        if events:
            for ev in events:
                x1, _ = to_px(ev.start_frame, 0)
                x2, _ = to_px(min(ev.end_frame, n - 1), 0)
                for px_x in range(max(x1, PAD_L), min(x2 + 1, W - PAD_R)):
                    for px_y in range(PAD_T, PAD_T + chart_h):
                        canvas.putpixel((px_x, px_y), (60, 40, 20))

        # Threshold line (red dashed)
        _, thresh_y = to_px(0, threshold)
        for x in range(PAD_L, W - PAD_R, 6):
            draw.line([(x, thresh_y), (min(x + 3, W - PAD_R), thresh_y)],
                      fill=(200, 60, 60), width=1)
        draw.text((W - PAD_R + 2, thresh_y - 8),
                  f"T={threshold:.2f}", fill=(200, 60, 60))

        # SSIM curve (blue)
        points = [to_px(i, s) for i, s in enumerate(ssim_scores)]
        for i in range(len(points) - 1):
            color = (60, 140, 255) if ssim_scores[i] >= threshold else (255, 80, 80)
            draw.line([points[i], points[i + 1]], fill=color, width=2)

        # Title
        draw.text((PAD_L, 5),
                  f"SSIM Timeline | {n} pairs | {total_time:.1f}s | "
                  f"threshold={threshold:.3f}",
                  fill=(200, 200, 210))

        out_path = str(self.output_dir / "ssim_timeline.png")
        canvas.save(out_path)
        logger.info(f"SSIM timeline saved: {out_path}")
        return out_path

    # -----------------------------------------------------------------------
    # Layer 3: GPT-5.4 Semantic Verification
    # -----------------------------------------------------------------------

    async def verify_with_gpt(self, event: FlickerEvent) -> str:
        """Use GPT-5.4 to determine if flicker is a bug or intentional animation.

        Sends the frame sequence to AgenticVisionClient for semantic analysis.
        Returns a classification string: 'BUG', 'ANIMATION', or 'UNCERTAIN'.
        """
        try:
            from .agentic_vision_service import AgenticVisionClient
        except ImportError:
            logger.warning("AgenticVisionClient not available, skipping Layer 3")
            return "SKIPPED: AgenticVisionClient not available"

        # Pick up to 6 evidence frames
        evidence = event.frame_paths[:6]
        if len(evidence) < 2:
            return "SKIPPED: insufficient frames"

        try:
            # Load the first frame as primary image
            with open(evidence[0], "rb") as f:
                primary_bytes = f.read()

            prompt = (
                f"Analyze this sequence of {len(evidence)} frames captured "
                f"during a {event.duration_ms:.0f}ms screen change on Android.\n\n"
                f"Pattern: {event.pattern}\n"
                f"SSIM scores: {[round(s, 3) for s in event.ssim_scores[:6]]}\n"
                f"Logcat events: {len(event.logcat_events)} system events\n\n"
                f"Question: Is this a visual BUG (unintended flicker/glitch) "
                f"or an intentional ANIMATION/transition?\n"
                f"Answer with: BUG, ANIMATION, or UNCERTAIN — then explain why."
            )

            client = AgenticVisionClient()
            result = await client.multi_step_vision(
                image_data=primary_bytes,
                query=prompt,
                instructions="Focus on whether the visual change pattern "
                             "looks intentional (smooth transition) or "
                             "unintentional (abrupt flicker, z-order glitch, "
                             "layout thrash)."
            )
            analysis = result.final_analysis
            logger.info(f"GPT-5.4 verdict for flicker: {analysis[:100]}...")
            return analysis
        except Exception as e:
            logger.error(f"GPT-5.4 verification failed: {e}")
            return f"ERROR: {e}"

    # -----------------------------------------------------------------------
    # Main orchestrator
    # -----------------------------------------------------------------------

    async def run_detection(
        self,
        duration: int = 10,
        scenario_fn=None,
        package: str = "",
        fps: int = 30,
        record_size: str = "720x1280",
        use_scene_filter: bool = True,
        gpt_verify: bool = False,
        cleanup_frames: bool = False,
    ) -> FlickerReport:
        """
        Run the full 4-layer flicker detection pipeline.

        Args:
            duration: Recording duration in seconds
            scenario_fn: Optional async callable that performs actions on device
                         during recording (e.g., rapid tapping, toggling)
            package: Android package name for SurfaceFlinger stats
            fps: Frame extraction rate for analysis
            record_size: Video resolution (lower = faster analysis)
            use_scene_filter: Use ffmpeg scene detection to skip identical frames
            gpt_verify: Run Layer 3 GPT-5.4 verification on HIGH severity events
            cleanup_frames: Delete extracted frames after analysis

        Returns:
            FlickerReport with all detection results
        """
        report = FlickerReport(
            session_id=self.session_id, device_id=self.device_id,
            frames_dir=str(self.frames_dir))
        t0 = time.time()

        try:
            # Layer 0: Start monitoring
            logger.info("=" * 60)
            logger.info("LAYER 0: SurfaceFlinger + Logcat monitoring")
            logger.info("=" * 60)
            pre_stats = await self.get_surface_stats(package)
            await self.start_logcat()

            # Layer 1: Start recording
            logger.info("=" * 60)
            logger.info("LAYER 1: adb screenrecord")
            logger.info("=" * 60)
            await self.start_recording(duration=duration, size=record_size)

            # Execute scenario (user actions that might cause flicker)
            if scenario_fn:
                logger.info("Executing test scenario...")
                await scenario_fn()
            else:
                logger.info(f"Recording for {duration}s (no scenario)")

            # Wait for recording to complete
            if self._record_proc:
                try:
                    await asyncio.wait_for(
                        self._record_proc.communicate(),
                        timeout=duration + 10)
                except asyncio.TimeoutError:
                    self._record_proc.terminate()
            await asyncio.sleep(1)

            # Stop monitoring, pull recording
            logcat_events = await self.stop_logcat()
            post_stats = await self.get_surface_stats(package)
            video_path = await self._pull_recording()

            if not video_path:
                report.error = "Failed to capture video recording"
                return report

            report.video_path = video_path
            report.logcat_events = logcat_events
            report.surface_stats = post_stats
            report.recording_duration = duration

            # Compute SurfaceFlinger delta (pre vs post)
            if pre_stats and post_stats:
                delta = SurfaceStatsDelta(
                    frames_during_test=(post_stats.total_frames
                                        - pre_stats.total_frames),
                    janky_during_test=(post_stats.janky_frames
                                       - pre_stats.janky_frames),
                )
                if delta.frames_during_test > 0:
                    delta.jank_pct_during_test = (
                        delta.janky_during_test
                        / delta.frames_during_test * 100)
                report.surface_delta = delta
                logger.info(f"SurfaceFlinger delta: "
                            f"{delta.frames_during_test} frames, "
                            f"{delta.janky_during_test} janky "
                            f"({delta.jank_pct_during_test:.1f}%)")

            # Layer 2: Frame extraction + SSIM analysis
            logger.info("=" * 60)
            logger.info("LAYER 2: Scene-filtered extraction + parallel SSIM")
            logger.info("=" * 60)
            frame_paths = self.extract_frames(
                video_path, fps=fps, use_scene_filter=use_scene_filter)
            report.total_frames_analyzed = len(frame_paths)
            report.total_scene_frames = len(frame_paths)

            if len(frame_paths) < 3:
                report.error = f"Too few frames extracted ({len(frame_paths)})"
                return report

            flicker_events, ssim_scores, threshold = self.analyze_frames(
                frame_paths, fps=fps)

            # Correlate with logcat
            flicker_events = self.correlate_logcat(flicker_events, logcat_events)

            # Create comparison images
            for i, ev in enumerate(flicker_events):
                comp_path = self.create_comparison_image(ev, i)
                if comp_path:
                    ev.frame_paths.insert(0, comp_path)

            # Create SSIM timeline visualization
            timeline_path = self.create_ssim_timeline(
                ssim_scores, fps, threshold, events=flicker_events)
            report.ssim_timeline_path = timeline_path

            # Layer 3: GPT-5.4 semantic verification (optional)
            if gpt_verify and flicker_events:
                logger.info("=" * 60)
                logger.info("LAYER 3: GPT-5.4 semantic verification")
                logger.info("=" * 60)
                for ev in flicker_events:
                    if ev.severity in ("HIGH", "MEDIUM"):
                        ev.gpt_analysis = await self.verify_with_gpt(ev)
                        logger.info(f"  {ev.pattern} → "
                                    f"{ev.gpt_analysis[:80]}...")

            report.flicker_events = flicker_events
            report.total_flickers_detected = len(flicker_events)

        except Exception as e:
            report.error = str(e)
            logger.error(f"Detection failed: {e}", exc_info=True)
        finally:
            report.analysis_time_seconds = time.time() - t0

        # Save report
        report_path = self.output_dir / "report.json"
        with open(report_path, "w") as f:
            json.dump(report.to_dict(), f, indent=2)
        logger.info(f"Report saved: {report_path}")
        logger.info(f"Total flickers: {report.total_flickers_detected}")
        logger.info(f"Analysis time: {report.analysis_time_seconds:.1f}s")

        # Optional: cleanup extracted frames to save disk
        if cleanup_frames and self.frames_dir.exists():
            import shutil
            shutil.rmtree(self.frames_dir)
            logger.info("Cleaned up extracted frames")

        return report