"""ActionSpan service — clip extraction and scoring.

Captures 2-3 second verification clips per agent action using adb screenrecord,
then computes a composite score (visual-change + stability) as a verification receipt.

No ML inference required — pure pixel comparison via numpy so scores are cheap,
deterministic, and fast (< 200ms on a local machine).
"""

import asyncio
import json as _json
import logging
import os
import subprocess
import time
import uuid
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Type

from pydantic import BaseModel as _BaseModel

from .action_span_models import (
    ActionSpan,
    ActionSpanManifest,
    ActionSpanStatus,
    ActionType,
    ScoreSpanRequest,
    StartSpanRequest,
    StartSpanResponse,
    now_iso_utc,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# JSON-backed persistent store (survives restarts)
# ---------------------------------------------------------------------------

_DATA_DIR = Path(__file__).parent.parent.parent / "data"


class _JsonBackedStore:
    """Dict-like store that persists Pydantic models to a JSON file on every write."""

    def __init__(self, path: Path, model_cls: Type[_BaseModel]) -> None:
        self._path = path
        self._model_cls = model_cls
        self._data: Dict[str, _BaseModel] = {}
        self._load()

    def _load(self) -> None:
        try:
            if self._path.exists():
                raw = _json.loads(self._path.read_text(encoding="utf-8"))
                self._data = {k: self._model_cls(**v) for k, v in raw.items()}
        except Exception as exc:
            logger.warning("Could not load %s: %s", self._path, exc)
            self._data = {}

    def _save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            payload = {k: v.model_dump() for k, v in self._data.items()}
            self._path.write_text(_json.dumps(payload, indent=2, default=str), encoding="utf-8")
        except Exception as exc:
            logger.warning("Could not persist %s: %s", self._path, exc)

    def __getitem__(self, key: str):
        return self._data[key]

    def __setitem__(self, key: str, value) -> None:
        self._data[key] = value
        self._save()

    def __contains__(self, key: object) -> bool:
        return key in self._data

    def get(self, key: str, default=None):
        return self._data.get(key, default)

    def values(self):
        return self._data.values()

    def items(self):
        return self._data.items()

    def __len__(self) -> int:
        return len(self._data)

    def clear(self) -> None:
        self._data.clear()
        self._save()


_span_store: _JsonBackedStore = _JsonBackedStore(_DATA_DIR / "action_spans.json", ActionSpan)
_manifest_store: _JsonBackedStore = _JsonBackedStore(_DATA_DIR / "action_span_manifests.json", ActionSpanManifest)
_recording_procs: Dict[str, subprocess.Popen] = {}   # span_id → adb proc (runtime only)

# Default clip directory relative to backend root
_DEFAULT_CLIP_DIR = Path(__file__).parent.parent.parent / "clips" / "action_spans"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _find_adb() -> Optional[str]:
    for candidate in ["adb", os.path.expanduser("~/Library/Android/sdk/platform-tools/adb")]:
        try:
            subprocess.run([candidate, "version"], capture_output=True, timeout=2, check=True)
            return candidate
        except Exception:
            pass
    return None


def _detect_device_id(adb: Optional[str]) -> Optional[str]:
    """Auto-detect the first connected ADB device/emulator."""
    if not adb:
        return None
    try:
        result = subprocess.run(
            [adb, "devices"], capture_output=True, text=True, timeout=5
        )
        for line in result.stdout.strip().split("\n")[1:]:
            parts = line.split()
            if len(parts) >= 2 and parts[1] == "device":
                return parts[0]
    except Exception:
        pass
    return None


def _sync_span_to_convex(span: "ActionSpan") -> None:
    """Best-effort sync of a scored span to Convex (fire-and-forget)."""
    convex_url = os.environ.get("CONVEX_URL") or os.environ.get("VITE_CONVEX_URL")
    if not convex_url:
        return
    try:
        import httpx

        # Parse ISO timestamps to epoch ms
        def _iso_to_ms(iso_str: Optional[str]) -> Optional[int]:
            if not iso_str:
                return None
            from datetime import datetime, timezone
            dt = datetime.fromisoformat(iso_str)
            return int(dt.timestamp() * 1000)

        payload = {
            "path": "actionSpans:upsertSpan",
            "args": {
                "spanId": span.span_id,
                "sessionId": span.session_id,
                "actionType": span.action_type.value if hasattr(span.action_type, 'value') else str(span.action_type),
                "actionDescription": span.action_description or "",
                "startedAt": _iso_to_ms(span.started_at) or int(time.time() * 1000),
                "endedAt": _iso_to_ms(span.ended_at),
                "durationMs": span.duration_ms,
                "clipPath": span.clip_path,
                "beforeScreenshot": span.before_screenshot,
                "afterScreenshot": span.after_screenshot,
                "frameCount": span.frame_count,
                "status": span.status.value if hasattr(span.status, 'value') else str(span.status),
                "visualChangeScore": span.visual_change_score,
                "stabilityScore": span.stability_score,
                "compositeScore": span.composite_score,
                "scoreRationale": span.score_rationale or "",
                "verified": span.verified,
                "error": span.error,
            },
        }
        # Remove None values — Convex doesn't accept null for non-optional fields
        payload["args"] = {k: v for k, v in payload["args"].items() if v is not None}

        httpx.post(
            f"{convex_url}/api/mutation",
            json=payload,
            timeout=5,
        )
        logger.debug("Synced span %s to Convex", span.span_id)
    except Exception as exc:
        logger.debug("Convex sync failed (best-effort): %s", exc)


def _sync_manifest_to_convex(manifest: "ActionSpanManifest") -> None:
    """Best-effort sync of manifest to Convex."""
    convex_url = os.environ.get("CONVEX_URL") or os.environ.get("VITE_CONVEX_URL")
    if not convex_url:
        return
    try:
        import httpx

        payload = {
            "path": "actionSpans:updateManifest",
            "args": {
                "sessionId": manifest.session_id,
                "totalSpans": manifest.total_spans,
                "scoredSpans": manifest.scored_spans,
                "verifiedSpans": manifest.verified_spans,
                "failedSpans": manifest.failed_spans,
                "passRate": float(manifest.pass_rate),
                "averageCompositeScore": float(manifest.average_composite_score),
                "updatedAt": int(time.time() * 1000),
            },
        }

        httpx.post(
            f"{convex_url}/api/mutation",
            json=payload,
            timeout=5,
        )
        logger.debug("Synced manifest for session %s to Convex", manifest.session_id)
    except Exception as exc:
        logger.debug("Convex manifest sync failed (best-effort): %s", exc)


def _pixel_diff_score(path_a: str, path_b: str) -> float:
    """Return 0.0 (identical) → 1.0 (completely different) using mean absolute pixel diff."""
    try:
        import numpy as np
        from PIL import Image

        a = np.asarray(Image.open(path_a).convert("RGB"), dtype=float)
        b = np.asarray(Image.open(path_b).convert("RGB"), dtype=float)
        if a.shape != b.shape:
            b_img = Image.open(path_b).convert("RGB").resize(
                (a.shape[1], a.shape[0]), Image.LANCZOS
            )
            b = np.asarray(b_img, dtype=float)
        diff = float(np.mean(np.abs(a - b))) / 255.0
        return min(diff * 5.0, 1.0)          # amplify small diffs; cap at 1.0
    except Exception as exc:
        logger.warning("pixel_diff_score failed: %s", exc)
        return 0.5                           # neutral fallback


def _ffmpeg_available() -> bool:
    """Check whether ffmpeg is on PATH."""
    try:
        subprocess.run(
            ["ffmpeg", "-version"], capture_output=True, timeout=5, check=True
        )
        return True
    except Exception:
        return False


def _extract_frames(clip_path: str, out_dir: Path, fps: int = 5) -> int:
    """Extract frames from clip via ffmpeg; return count.

    Returns 0 (and logs a clear reason) when ffmpeg is missing so the caller
    can fall back to screenshot-only scoring.
    """
    if not _ffmpeg_available():
        logger.info(
            "FFmpeg not available — skipping frame extraction; "
            "scoring will use screenshot-only mode"
        )
        return 0

    out_dir.mkdir(parents=True, exist_ok=True)
    pattern = str(out_dir / "frame_%04d.jpg")
    cmd = ["ffmpeg", "-y", "-i", clip_path, "-vf", f"fps={fps}", "-q:v", "5", pattern]
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=30)
        count = len(list(out_dir.glob("frame_*.jpg")))
        if count == 0 and result.returncode != 0:
            logger.warning(
                "ffmpeg exited with code %d — no frames extracted; stderr: %s",
                result.returncode,
                result.stderr.decode(errors="replace")[:300],
            )
        return count
    except subprocess.TimeoutExpired:
        logger.warning("ffmpeg frame extraction timed out after 30s")
        return 0
    except Exception as exc:
        logger.warning("ffmpeg frame extraction failed: %s", exc)
        return 0


def _stability_score_from_frames(frame_dir: Path) -> float:
    """Compute inter-frame stability (1 = stable, 0 = constantly changing)."""
    frames = sorted(frame_dir.glob("frame_*.jpg"))
    if len(frames) < 2:
        return 1.0
    diffs = []
    for a, b in zip(frames, frames[1:]):
        diffs.append(_pixel_diff_score(str(a), str(b)))
    mean_diff = sum(diffs) / len(diffs)
    return max(0.0, 1.0 - mean_diff)


# ---------------------------------------------------------------------------
# ActionSpanService
# ---------------------------------------------------------------------------

class ActionSpanService:
    """Manages the full lifecycle of ActionSpans: capture → score → manifest."""

    def __init__(self, clip_dir: Optional[Path] = None):
        self.clip_dir = clip_dir or _DEFAULT_CLIP_DIR
        self.clip_dir.mkdir(parents=True, exist_ok=True)
        self.adb = _find_adb()
        self._default_device_id = _detect_device_id(self.adb)
        if self._default_device_id:
            logger.info("Auto-detected device: %s", self._default_device_id)
        if not self.adb:
            logger.warning("ADB not found — ActionSpan video capture disabled")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start_span(self, req: StartSpanRequest) -> StartSpanResponse:
        """Create a new span and begin adb screenrecord (best-effort)."""
        span_id = str(uuid.uuid4())
        started_at = now_iso_utc()

        clip_dir = self.clip_dir / req.session_id / span_id
        clip_dir.mkdir(parents=True, exist_ok=True)
        clip_path = str(clip_dir / "clip.mp4")

        # Auto-detect device if not provided
        device_id = req.device_id or self._default_device_id
        if not device_id and self.adb:
            device_id = _detect_device_id(self.adb)
            self._default_device_id = device_id

        span = ActionSpan(
            span_id=span_id,
            session_id=req.session_id,
            action_type=req.action_type,
            action_description=req.action_description,
            started_at=started_at,
            clip_path=clip_path,
            status=ActionSpanStatus.CAPTURING,
        )
        _span_store[span_id] = span

        # Build a descriptive message about recording state
        recording_message: str

        if not self.adb:
            recording_message = (
                "ActionSpan created (video capture disabled — ADB not found). "
                "Scoring will use screenshot-only mode."
            )
            logger.info("Span %s: ADB not available, screenshot-only mode", span_id)
        elif not device_id:
            recording_message = (
                "ActionSpan created (video capture skipped — no Android device/emulator connected). "
                "Connect a device or launch an emulator, then retry."
            )
            logger.warning("Span %s: no device connected, cannot record", span_id)
        else:
            # Best-effort screen recording — auto-uses detected device
            try:
                proc = subprocess.Popen(
                    [self.adb, "-s", device_id, "shell", "screenrecord",
                     "--time-limit", "5", "/sdcard/ta_span.mp4"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
                _recording_procs[span_id] = proc
                recording_message = f"ActionSpan recording started on device {device_id}"
                logger.info("Started screenrecord for span %s on %s", span_id, device_id)
            except Exception as exc:
                recording_message = (
                    f"ActionSpan created but screenrecord failed to start: {exc}. "
                    "Scoring will use screenshot-only mode."
                )
                logger.warning("screenrecord failed to start: %s", exc)

        return StartSpanResponse(
            span_id=span_id,
            session_id=req.session_id,
            status=ActionSpanStatus.CAPTURING,
            started_at=started_at,
            message=recording_message,
        )

    def score_span(self, req: ScoreSpanRequest) -> ActionSpan:
        """Stop recording, pull clip, compute score, update span."""
        span = _span_store.get(req.span_id)
        if not span:
            raise ValueError(f"Span not found: {req.span_id}")

        span.status = ActionSpanStatus.PROCESSING
        ended_at = now_iso_utc()
        span.ended_at = ended_at

        # Stop recording proc
        proc = _recording_procs.pop(req.span_id, None)
        if proc:
            try:
                proc.terminate()
                proc.wait(timeout=3)
            except Exception:
                pass

        # Pull clip from device
        clip_path = req.clip_path or span.clip_path
        if clip_path and self.adb:
            self._pull_clip(req.span_id, clip_path)

        # Extract frames and score
        frame_dir = Path(clip_path).parent / "frames" if clip_path else Path(self.clip_dir) / "frames"
        has_clip = clip_path and Path(clip_path).exists()
        frame_count = _extract_frames(clip_path, frame_dir) if has_clip else 0
        span.frame_count = frame_count

        # Determine scoring mode
        has_screenshots = bool(span.before_screenshot and span.after_screenshot)
        scoring_mode = "full"  # clip + screenshots
        if not has_clip and not has_screenshots:
            scoring_mode = "fallback"
        elif not has_clip or frame_count == 0:
            scoring_mode = "screenshot_only"

        visual_change = _pixel_diff_score(span.before_screenshot, span.after_screenshot) \
            if has_screenshots else 0.5
        stability = _stability_score_from_frames(frame_dir) if frame_count >= 2 else 1.0

        # Composite: action should change screen (vc > 0.1) and be stable (stability > 0.7)
        composite = (visual_change * 0.5) + (stability * 0.5)
        threshold = req.score_threshold

        span.visual_change_score = round(visual_change, 3)
        span.stability_score = round(stability, 3)
        span.composite_score = round(composite, 3)
        span.verified = composite >= threshold
        span.status = ActionSpanStatus.SCORED

        mode_note = ""
        if scoring_mode == "screenshot_only":
            mode_note = " [screenshot-only: no clip/ffmpeg, stability defaults to 1.0]"
        elif scoring_mode == "fallback":
            mode_note = " [fallback: no screenshots or clip available, using neutral defaults]"

        span.score_rationale = (
            f"visual_change={visual_change:.2f} stability={stability:.2f} "
            f"composite={composite:.2f} threshold={threshold} "
            f"→ {'PASS' if span.verified else 'FAIL'}{mode_note}"
        )

        _span_store[req.span_id] = span
        self._update_manifest(span)

        # Sync to Convex (best-effort, non-blocking)
        _sync_span_to_convex(span)

        return span

    def get_span(self, span_id: str) -> Optional[ActionSpan]:
        return _span_store.get(span_id)

    def list_spans(self, session_id: str):
        return [s for s in _span_store.values() if s.session_id == session_id]

    def get_manifest(self, session_id: str) -> ActionSpanManifest:
        if session_id not in _manifest_store:
            _manifest_store[session_id] = ActionSpanManifest(
                session_id=session_id,
                created_at=now_iso_utc(),
                updated_at=now_iso_utc(),
            )
        return _manifest_store[session_id]

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _pull_clip(self, span_id: str, local_path: str):
        """Pull screenrecord from device (best-effort)."""
        if not self.adb:
            return
        try:
            subprocess.run(
                [self.adb, "pull", "/sdcard/ta_span.mp4", local_path],
                capture_output=True, timeout=15,
            )
            logger.info("Pulled clip for span %s → %s", span_id, local_path)
        except Exception as exc:
            logger.warning("clip pull failed: %s", exc)

    def _update_manifest(self, span: ActionSpan):
        """Recompute manifest aggregates after each scored span."""
        m = self.get_manifest(span.session_id)
        spans = self.list_spans(span.session_id)

        m.total_spans = len(spans)
        m.scored_spans = sum(1 for s in spans if s.status == ActionSpanStatus.SCORED)
        m.verified_spans = sum(1 for s in spans if s.verified is True)
        m.failed_spans = sum(1 for s in spans if s.verified is False)
        m.pass_rate = (m.verified_spans / m.scored_spans) if m.scored_spans else 0.0
        scored = [s.composite_score for s in spans if s.composite_score is not None]
        m.average_composite_score = round(sum(scored) / len(scored), 3) if scored else 0.0
        m.spans = spans
        m.updated_at = now_iso_utc()
        _manifest_store[span.session_id] = m

        # Sync manifest to Convex
        _sync_manifest_to_convex(m)

        # Auto-release any linked validation hooks if all spans pass
        try:
            from ...api.validation_hooks import try_auto_release_hooks_for_session
            try_auto_release_hooks_for_session(span.session_id, m.pass_rate, m.scored_spans)
        except Exception as exc:
            logger.debug("Hook auto-release check failed (non-fatal): %s", exc)


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------
action_span_service = ActionSpanService()

