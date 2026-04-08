"""Narrated demo walkthrough generation for mobile device recordings.

This service records a walkthrough with ``adb screenrecord``, generates text-to-
speech narration with the OpenAI Python SDK, writes subtitles/manifest artifacts,
and muxes a final narrated MP4 with ffmpeg.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import subprocess
import wave
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Awaitable, Callable, Mapping, Optional, Sequence

from app.agents.device_testing.flicker_detection_service import (
    FlickerDetectionService,
)
from app.observability.tracing import get_traced_client

logger = logging.getLogger(__name__)


@dataclass
class NarrationSegment:
    """A single narrated walkthrough step."""

    text: str
    title: str = ""
    pause_after_ms: int = 350


@dataclass
class TimedNarrationSegment:
    """Narration segment with resolved timing for subtitles/manifests."""

    index: int
    title: str
    text: str
    audio_path: str
    start_seconds: float
    end_seconds: float
    duration_seconds: float


@dataclass
class NarratedWalkthroughResult:
    """Artifacts produced by a narrated walkthrough run."""

    session_id: str
    output_dir: str
    raw_video_path: str
    final_video_path: str
    narration_audio_path: str
    subtitles_path: str
    manifest_path: str
    video_duration_seconds: float
    narration_duration_seconds: float
    video_padding_seconds: float
    segments: list[TimedNarrationSegment]

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable representation of the result."""
        return asdict(self)


@dataclass
class _SynthesizedSegment:
    index: int
    title: str
    text: str
    audio_path: Path
    duration_seconds: float
    pause_after_ms: int


@dataclass
class _NarrationArtifacts:
    audio_path: Path
    subtitles_path: Path
    duration_seconds: float
    segments: list[TimedNarrationSegment]


class NarratedWalkthroughService:
    """Generate narrated demo walkthrough videos from recorded device sessions."""

    DEFAULT_MODEL = "tts-1"
    DEFAULT_VOICE = "alloy"
    DEFAULT_RESPONSE_FORMAT = "mp3"
    MAX_TTS_CHARS = 4096
    MAX_SYNTHESIS_CONCURRENCY = 3

    def __init__(
        self,
        device_id: str,
        output_dir: Optional[str] = None,
        api_key: Optional[str] = None,
        model: str = DEFAULT_MODEL,
        voice: str = DEFAULT_VOICE,
        response_format: str = DEFAULT_RESPONSE_FORMAT,
        speed: float = 1.0,
    ) -> None:
        self.device_id = device_id
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")
        self.model = model
        self.voice = voice
        self.response_format = response_format
        self.speed = speed
        self.session_id = f"walkthrough_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        self.output_dir = (
            Path(output_dir)
            if output_dir
            else Path(__file__).parent.parent.parent.parent
            / "screenshots"
            / "demo_walkthroughs"
            / self.session_id
        )
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.audio_dir = self.output_dir / "audio"
        self.segments_dir = self.audio_dir / "segments"
        self.chunks_dir = self.audio_dir / "chunks"
        self.silence_dir = self.audio_dir / "silence"
        for path in (
            self.audio_dir,
            self.segments_dir,
            self.chunks_dir,
            self.silence_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)

        self.recorder = FlickerDetectionService(
            device_id=device_id,
            output_dir=str(self.output_dir),
            adaptive_threshold=True,
        )
        self._client: Any = None
        logger.info(
            "NarratedWalkthroughService initialized: device=%s output=%s",
            self.device_id,
            self.output_dir,
        )

    async def generate_walkthrough(
        self,
        segments: Sequence[NarrationSegment | Mapping[str, Any]],
        duration: int,
        scenario_fn: Optional[Callable[[], Awaitable[None]]] = None,
        record_size: str = "720x1280",
        bitrate: str = "8M",
        stop_when_scenario_complete: bool = True,
    ) -> NarratedWalkthroughResult:
        """Record a walkthrough and produce narrated artifacts.

        Args:
            segments: Narration content in playback order.
            duration: Maximum recording length in seconds.
            scenario_fn: Optional coroutine that drives the device while recording.
            record_size: ``adb screenrecord`` size argument.
            bitrate: ``adb screenrecord`` bitrate.
            stop_when_scenario_complete: Stop recording immediately after the
                scenario finishes instead of waiting the full duration.

        Returns:
            NarratedWalkthroughResult with artifact paths and timing metadata.
        """
        normalized_segments = self.normalize_segments(segments)
        recording_task = asyncio.create_task(
            self._record_walkthrough(
                duration=duration,
                scenario_fn=scenario_fn,
                record_size=record_size,
                bitrate=bitrate,
                stop_when_scenario_complete=stop_when_scenario_complete,
            )
        )
        narration_task = asyncio.create_task(
            self._build_narration_artifacts(normalized_segments)
        )

        raw_video_path, narration = await asyncio.gather(
            recording_task,
            narration_task,
        )

        video_duration = await asyncio.to_thread(self._probe_duration, raw_video_path)
        final_video_path, padding_seconds = await asyncio.to_thread(
            self._mux_video_with_audio,
            raw_video_path,
            narration.audio_path,
            narration.duration_seconds,
            video_duration,
        )
        manifest_path = self._write_manifest(
            raw_video_path=raw_video_path,
            final_video_path=final_video_path,
            narration=narration,
            video_duration_seconds=video_duration,
            video_padding_seconds=padding_seconds,
        )

        return NarratedWalkthroughResult(
            session_id=self.session_id,
            output_dir=str(self.output_dir),
            raw_video_path=str(raw_video_path),
            final_video_path=str(final_video_path),
            narration_audio_path=str(narration.audio_path),
            subtitles_path=str(narration.subtitles_path),
            manifest_path=str(manifest_path),
            video_duration_seconds=video_duration,
            narration_duration_seconds=narration.duration_seconds,
            video_padding_seconds=padding_seconds,
            segments=narration.segments,
        )

    def normalize_segments(
        self,
        segments: Sequence[NarrationSegment | Mapping[str, Any]],
    ) -> list[NarrationSegment]:
        """Normalize input segment payloads into NarrationSegment objects."""
        normalized: list[NarrationSegment] = []
        for index, segment in enumerate(segments, start=1):
            if isinstance(segment, NarrationSegment):
                current = segment
            else:
                current = NarrationSegment(
                    text=str(segment.get("text", "")).strip(),
                    title=str(segment.get("title", "")).strip(),
                    pause_after_ms=int(segment.get("pause_after_ms", 350)),
                )
            if not current.text:
                raise ValueError(f"Narration segment {index} is missing text")
            if current.pause_after_ms < 0:
                raise ValueError(
                    f"Narration segment {index} has negative pause_after_ms"
                )
            if not current.title:
                current = NarrationSegment(
                    text=current.text,
                    title=f"Step {index}",
                    pause_after_ms=current.pause_after_ms,
                )
            normalized.append(current)
        if not normalized:
            raise ValueError("At least one narration segment is required")
        return normalized

    def chunk_text(self, text: str, max_chars: Optional[int] = None) -> list[str]:
        """Split narration text into TTS-safe chunks while preserving sentences."""
        limit = max_chars or self.MAX_TTS_CHARS
        cleaned = re.sub(r"\s+", " ", text).strip()
        if not cleaned:
            return []
        if len(cleaned) <= limit:
            return [cleaned]

        sentences = re.split(r"(?<=[.!?])\s+", cleaned)
        if len(sentences) == 1:
            return self._chunk_words(cleaned, limit)

        chunks: list[str] = []
        current: list[str] = []
        for sentence in sentences:
            if len(sentence) > limit:
                if current:
                    chunks.append(" ".join(current))
                    current = []
                chunks.extend(self._chunk_words(sentence, limit))
                continue
            candidate = sentence if not current else f"{' '.join(current)} {sentence}"
            if len(candidate) <= limit:
                current.append(sentence)
                continue
            chunks.append(" ".join(current))
            current = [sentence]
        if current:
            chunks.append(" ".join(current))
        return chunks

    @staticmethod
    def format_srt_timestamp(seconds: float) -> str:
        """Convert seconds to SRT timestamp format."""
        total_ms = max(0, int(round(seconds * 1000)))
        hours, rem = divmod(total_ms, 3_600_000)
        minutes, rem = divmod(rem, 60_000)
        secs, millis = divmod(rem, 1000)
        return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"

    def render_srt(self, segments: Sequence[TimedNarrationSegment]) -> str:
        """Render SRT subtitle content for the timed narration segments."""
        blocks = []
        for segment in segments:
            blocks.append(
                "\n".join(
                    [
                        str(segment.index),
                        (
                            f"{self.format_srt_timestamp(segment.start_seconds)} --> "
                            f"{self.format_srt_timestamp(segment.end_seconds)}"
                        ),
                        segment.text,
                    ]
                )
            )
        return "\n\n".join(blocks) + "\n"

    async def _record_walkthrough(
        self,
        duration: int,
        scenario_fn: Optional[Callable[[], Awaitable[None]]],
        record_size: str,
        bitrate: str,
        stop_when_scenario_complete: bool,
    ) -> Path:
        """Record the device walkthrough video."""
        await self.recorder.start_recording(
            duration=duration,
            size=record_size,
            bitrate=bitrate,
        )
        scenario_error: Optional[Exception] = None
        if scenario_fn:
            try:
                await scenario_fn()
            except Exception as exc:  # pragma: no cover - defensive cleanup path
                scenario_error = exc

        if scenario_fn and stop_when_scenario_complete:
            local_path = await self.recorder.stop_recording()
        else:
            local_path = await self.recorder.wait_for_recording(duration)

        if scenario_error is not None:
            raise scenario_error
        if not local_path:
            raise RuntimeError("Failed to capture walkthrough recording")
        return Path(local_path)

    async def _build_narration_artifacts(
        self,
        segments: Sequence[NarrationSegment],
    ) -> _NarrationArtifacts:
        """Synthesize segment audio, pauses, subtitles, and final narration."""
        semaphore = asyncio.Semaphore(self.MAX_SYNTHESIS_CONCURRENCY)

        async def synthesize(index: int, segment: NarrationSegment) -> _SynthesizedSegment:
            async with semaphore:
                return await self._synthesize_segment_audio(index, segment)

        synthesized = await asyncio.gather(
            *[
                synthesize(index, segment)
                for index, segment in enumerate(segments, start=1)
            ]
        )
        synthesized.sort(key=lambda item: item.index)

        concat_inputs: list[Path] = []
        for index, synthesized_segment in enumerate(synthesized):
            concat_inputs.append(synthesized_segment.audio_path)
            is_last = index == len(synthesized) - 1
            if is_last or synthesized_segment.pause_after_ms <= 0:
                continue
            silence_path = self.silence_dir / f"pause_{synthesized_segment.index:02d}.wav"
            await asyncio.to_thread(
                self._create_silence_file,
                synthesized_segment.pause_after_ms / 1000.0,
                silence_path,
            )
            concat_inputs.append(silence_path)

        final_audio_path = self.audio_dir / f"narration.{self.response_format}"
        await asyncio.to_thread(self._concat_audio_files, concat_inputs, final_audio_path)
        timed_segments = self._build_timed_segments(synthesized)

        subtitles_path = self.output_dir / "walkthrough.srt"
        subtitles_path.write_text(self.render_srt(timed_segments), encoding="utf-8")
        total_duration = await asyncio.to_thread(self._probe_duration, final_audio_path)
        return _NarrationArtifacts(
            audio_path=final_audio_path,
            subtitles_path=subtitles_path,
            duration_seconds=total_duration,
            segments=timed_segments,
        )

    async def _synthesize_segment_audio(
        self,
        index: int,
        segment: NarrationSegment,
    ) -> _SynthesizedSegment:
        """Synthesize one logical narration segment, chunking if necessary."""
        chunk_paths: list[Path] = []
        for chunk_index, chunk in enumerate(self.chunk_text(segment.text), start=1):
            chunk_path = (
                self.chunks_dir
                / f"segment_{index:02d}_chunk_{chunk_index:02d}.{self.response_format}"
            )
            await asyncio.to_thread(self._write_speech_file, chunk, chunk_path)
            chunk_paths.append(chunk_path)

        segment_path = self.segments_dir / f"segment_{index:02d}.{self.response_format}"
        if len(chunk_paths) == 1:
            shutil.copyfile(chunk_paths[0], segment_path)
        else:
            await asyncio.to_thread(self._concat_audio_files, chunk_paths, segment_path)

        duration_seconds = await asyncio.to_thread(self._probe_duration, segment_path)
        return _SynthesizedSegment(
            index=index,
            title=segment.title,
            text=segment.text,
            audio_path=segment_path,
            duration_seconds=duration_seconds,
            pause_after_ms=segment.pause_after_ms,
        )

    def _get_client(self) -> Any:
        """Return a traced OpenAI client for TTS generation."""
        if self._client is not None:
            return self._client
        if not self.api_key:
            raise ValueError("OPENAI_API_KEY is required for narrated walkthrough TTS")
        from openai import OpenAI

        self._client = get_traced_client(OpenAI(api_key=self.api_key))
        return self._client

    def _write_speech_file(self, text: str, output_path: Path) -> None:
        """Write synthesized speech for a text chunk to disk."""
        client = self._get_client()
        params: dict[str, Any] = {
            "model": self.model,
            "voice": self.voice,
            "input": text,
            "response_format": self.response_format,
            "speed": self.speed,
        }
        with client.audio.speech.with_streaming_response.create(**params) as response:
            response.stream_to_file(output_path)

    @staticmethod
    def _chunk_words(text: str, max_chars: int) -> list[str]:
        """Fallback chunking when sentence boundaries are insufficient."""
        words = text.split()
        chunks: list[str] = []
        current: list[str] = []
        for word in words:
            candidate = word if not current else f"{' '.join(current)} {word}"
            if len(candidate) <= max_chars:
                current.append(word)
                continue
            if current:
                chunks.append(" ".join(current))
            current = [word]
        if current:
            chunks.append(" ".join(current))
        return chunks

    def _build_timed_segments(
        self,
        segments: Sequence[_SynthesizedSegment],
    ) -> list[TimedNarrationSegment]:
        """Resolve timeline boundaries for each narration segment."""
        timed: list[TimedNarrationSegment] = []
        cursor = 0.0
        for index, segment in enumerate(segments):
            start_seconds = cursor
            end_seconds = start_seconds + segment.duration_seconds
            timed.append(
                TimedNarrationSegment(
                    index=segment.index,
                    title=segment.title,
                    text=segment.text,
                    audio_path=str(segment.audio_path),
                    start_seconds=start_seconds,
                    end_seconds=end_seconds,
                    duration_seconds=segment.duration_seconds,
                )
            )
            if index < len(segments) - 1:
                cursor = end_seconds + (segment.pause_after_ms / 1000.0)
            else:
                cursor = end_seconds
        return timed

    def _concat_audio_files(self, input_paths: Sequence[Path], output_path: Path) -> None:
        """Concatenate audio clips into a single file with ffmpeg."""
        if not input_paths:
            raise ValueError("No audio inputs provided for concatenation")
        if len(input_paths) == 1:
            shutil.copyfile(input_paths[0], output_path)
            return
        cmd = self._build_audio_concat_command(input_paths, output_path)
        self._run_subprocess(cmd, timeout=180)

    def _build_audio_concat_command(
        self,
        input_paths: Sequence[Path],
        output_path: Path,
    ) -> list[str]:
        """Build the ffmpeg command used for audio concatenation."""
        cmd = ["ffmpeg", "-y"]
        for path in input_paths:
            cmd.extend(["-i", str(path)])
        filter_inputs = "".join(f"[{index}:a]" for index in range(len(input_paths)))
        filter_complex = f"{filter_inputs}concat=n={len(input_paths)}:v=0:a=1[outa]"
        cmd.extend(
            [
                "-filter_complex",
                filter_complex,
                "-map",
                "[outa]",
                "-ar",
                "44100",
                "-ac",
                "1",
                str(output_path),
            ]
        )
        return cmd

    def _mux_video_with_audio(
        self,
        video_path: Path,
        audio_path: Path,
        narration_duration_seconds: float,
        video_duration_seconds: float,
    ) -> tuple[Path, float]:
        """Mux the recorded video and narration, padding video if needed."""
        padding_seconds = max(0.0, narration_duration_seconds - video_duration_seconds)
        final_video_path = self.output_dir / "walkthrough_narrated.mp4"
        cmd = self._build_mux_command(
            video_path=video_path,
            audio_path=audio_path,
            output_path=final_video_path,
            pad_seconds=padding_seconds,
        )
        self._run_subprocess(cmd, timeout=240)
        return final_video_path, padding_seconds

    def _build_mux_command(
        self,
        video_path: Path,
        audio_path: Path,
        output_path: Path,
        pad_seconds: float = 0.0,
    ) -> list[str]:
        """Build the ffmpeg command used to create the final narrated MP4."""
        if pad_seconds > 0:
            return [
                "ffmpeg",
                "-y",
                "-i",
                str(video_path),
                "-i",
                str(audio_path),
                "-filter_complex",
                f"[0:v]tpad=stop_mode=clone:stop_duration={pad_seconds:.3f}[v]",
                "-map",
                "[v]",
                "-map",
                "1:a:0",
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                "18",
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                str(output_path),
            ]
        return [
            "ffmpeg",
            "-y",
            "-i",
            str(video_path),
            "-i",
            str(audio_path),
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            str(output_path),
        ]

    def _write_manifest(
        self,
        raw_video_path: Path,
        final_video_path: Path,
        narration: _NarrationArtifacts,
        video_duration_seconds: float,
        video_padding_seconds: float,
    ) -> Path:
        """Persist JSON metadata for downstream automation or review."""
        manifest_path = self.output_dir / "walkthrough_manifest.json"
        payload = {
            "session_id": self.session_id,
            "device_id": self.device_id,
            "model": self.model,
            "voice": self.voice,
            "response_format": self.response_format,
            "artifacts": {
                "raw_video_path": str(raw_video_path),
                "final_video_path": str(final_video_path),
                "narration_audio_path": str(narration.audio_path),
                "subtitles_path": str(narration.subtitles_path),
            },
            "durations": {
                "video_seconds": video_duration_seconds,
                "narration_seconds": narration.duration_seconds,
                "video_padding_seconds": video_padding_seconds,
            },
            "segments": [asdict(segment) for segment in narration.segments],
        }
        manifest_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return manifest_path

    def _probe_duration(self, path: Path) -> float:
        """Return media duration in seconds using ffprobe, with WAV fallback."""
        try:
            result = subprocess.run(
                [
                    "ffprobe",
                    "-v",
                    "error",
                    "-show_entries",
                    "format=duration",
                    "-of",
                    "default=noprint_wrappers=1:nokey=1",
                    str(path),
                ],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0 and result.stdout.strip():
                return float(result.stdout.strip())
        except FileNotFoundError:
            logger.warning("ffprobe not available while probing %s", path)
        except (subprocess.TimeoutExpired, ValueError) as exc:
            logger.warning("Failed to probe media duration for %s: %s", path, exc)

        if path.suffix.lower() == ".wav":
            with wave.open(str(path), "rb") as wav_file:
                return wav_file.getnframes() / float(wav_file.getframerate())
        raise RuntimeError(f"Unable to determine duration for {path}")

    @staticmethod
    def _create_silence_file(duration_seconds: float, output_path: Path) -> None:
        """Create a PCM WAV silence clip for subtitle-aligned pauses."""
        sample_rate = 44100
        frame_count = int(duration_seconds * sample_rate)
        with wave.open(str(output_path), "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(sample_rate)
            wav_file.writeframes(b"\x00\x00" * frame_count)

    @staticmethod
    def _run_subprocess(cmd: Sequence[str], timeout: int) -> None:
        """Run a subprocess and raise a concise error on failure."""
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if result.returncode != 0:
            raise RuntimeError(
                f"Command failed ({result.returncode}): {' '.join(cmd)}\n"
                f"{result.stderr[:500]}"
            )
