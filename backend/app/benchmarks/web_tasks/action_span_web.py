"""
Phase 3 – ActionSpan for Playwright web benchmarks.

Ports the concept from the device ActionSpanService (ADB-based) to
Playwright browser screenshots.  Each span captures a before/after
screenshot pair around a single agent action, then scores the visual
change and layout stability.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, List
from datetime import datetime, timezone
import uuid
import logging
import json

import numpy as np
from PIL import Image

logger = logging.getLogger(__name__)


@dataclass
class WebActionSpan:
    span_id: str
    action_name: str
    status: str  # "pending", "capturing", "scored"
    before_path: Optional[str] = None
    after_path: Optional[str] = None
    visual_change_score: float = 0.0
    stability_score: float = 1.0
    combined_score: float = 0.0
    started_at: Optional[str] = None
    ended_at: Optional[str] = None
    error: Optional[str] = None


class WebActionSpanService:
    """Captures before/after screenshots around Playwright actions and
    computes visual-change and layout-stability scores."""

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def start_span(
        self, page, action_name: str, artifacts_dir: Path
    ) -> WebActionSpan:
        """Take a *before* screenshot and return a new pending span."""
        span = WebActionSpan(
            span_id=uuid.uuid4().hex[:12],
            action_name=action_name,
            status="capturing",
            started_at=datetime.now(timezone.utc).isoformat(),
        )

        artifacts_dir.mkdir(parents=True, exist_ok=True)
        before_path = str(
            artifacts_dir / f"{span.span_id}_before.png"
        )

        try:
            await page.screenshot(path=before_path, full_page=False)
            span.before_path = before_path
        except Exception as exc:
            logger.warning("start_span screenshot failed: %s", exc)
            span.error = str(exc)
            span.status = "pending"

        return span

    async def end_span(
        self, page, span: WebActionSpan, artifacts_dir: Path
    ) -> WebActionSpan:
        """Take an *after* screenshot, compute scores, return updated span."""
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        after_path = str(
            artifacts_dir / f"{span.span_id}_after.png"
        )

        try:
            await page.screenshot(path=after_path, full_page=False)
            span.after_path = after_path
        except Exception as exc:
            logger.warning("end_span screenshot failed: %s", exc)
            span.error = str(exc)
            span.status = "scored"
            span.ended_at = datetime.now(timezone.utc).isoformat()
            return span

        # --- Visual-change score ---
        if span.before_path and span.after_path:
            try:
                span.visual_change_score = self._pixel_diff(
                    span.before_path, span.after_path
                )
            except Exception as exc:
                logger.warning("pixel_diff failed: %s", exc)
                span.visual_change_score = 0.0

        # --- Layout-stability score ---
        try:
            cls_value = await self._layout_shift(page)
            span.stability_score = max(0.0, min(1.0, 1.0 - cls_value))
        except Exception as exc:
            logger.warning("layout_shift failed: %s", exc)
            span.stability_score = 1.0

        # --- Combined score ---
        span.combined_score = (
            span.visual_change_score * 0.5 + span.stability_score * 0.5
        )

        span.status = "scored"
        span.ended_at = datetime.now(timezone.utc).isoformat()
        return span

    # ------------------------------------------------------------------
    # Scoring helpers
    # ------------------------------------------------------------------

    def _pixel_diff(self, before_path: str, after_path: str) -> float:
        """PIL + numpy mean-absolute pixel diff, amplified 5x, capped at 1.0."""
        a = np.asarray(Image.open(before_path).convert("RGB"), dtype=float)
        b = np.asarray(Image.open(after_path).convert("RGB"), dtype=float)

        # Resize b to match a if shapes differ
        if a.shape != b.shape:
            b_img = Image.open(after_path).convert("RGB").resize(
                (a.shape[1], a.shape[0]), Image.LANCZOS
            )
            b = np.asarray(b_img, dtype=float)

        diff = float(np.mean(np.abs(a - b))) / 255.0
        return min(diff * 5.0, 1.0)

    async def _layout_shift(self, page) -> float:
        """Cumulative Layout Shift via the Performance API."""
        cls = await page.evaluate(
            """() => {
                const entries = performance.getEntriesByType('layout-shift');
                return entries.reduce((sum, e) => sum + e.value, 0);
            }"""
        )
        return float(cls)

    # ------------------------------------------------------------------
    # Manifest
    # ------------------------------------------------------------------

    def save_manifest(
        self, spans: List[WebActionSpan], artifacts_dir: Path
    ) -> str:
        """Persist an ``action_spans.json`` manifest and return its path."""
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = str(artifacts_dir / "action_spans.json")

        records = []
        for s in spans:
            records.append(
                {
                    "span_id": s.span_id,
                    "action_name": s.action_name,
                    "status": s.status,
                    "before_path": s.before_path,
                    "after_path": s.after_path,
                    "visual_change_score": s.visual_change_score,
                    "stability_score": s.stability_score,
                    "combined_score": s.combined_score,
                    "started_at": s.started_at,
                    "ended_at": s.ended_at,
                    "error": s.error,
                }
            )

        with open(manifest_path, "w") as f:
            json.dump(records, f, indent=2)

        logger.info("Saved %d action spans to %s", len(records), manifest_path)
        return manifest_path
