"""Unit tests for narrated walkthrough generation helpers."""

from pathlib import Path
import sys

# Add backend to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.agents.device_testing.demo_walkthrough_service import (
    NarratedWalkthroughService,
    TimedNarrationSegment,
    _SynthesizedSegment,
)


class TestNarratedWalkthroughService:
    """Focused unit tests for narration chunking and artifact helpers."""

    def test_chunk_text_respects_limit_and_sentence_boundaries(self, tmp_path):
        service = NarratedWalkthroughService("emulator-5554", output_dir=str(tmp_path))
        text = (
            "First sentence is short. Second sentence is also short. "
            "Third sentence should land in its own chunk."
        )

        chunks = service.chunk_text(text, max_chars=45)

        assert len(chunks) == 3
        assert all(len(chunk) <= 45 for chunk in chunks)
        assert chunks[0].endswith("short.")
        assert chunks[-1].startswith("Third sentence")

    def test_render_srt_uses_expected_timestamps(self, tmp_path):
        service = NarratedWalkthroughService("emulator-5554", output_dir=str(tmp_path))
        srt = service.render_srt(
            [
                TimedNarrationSegment(
                    index=1,
                    title="Intro",
                    text="Hello world",
                    audio_path="segment_01.mp3",
                    start_seconds=0.0,
                    end_seconds=1.25,
                    duration_seconds=1.25,
                ),
                TimedNarrationSegment(
                    index=2,
                    title="Next",
                    text="Second step",
                    audio_path="segment_02.mp3",
                    start_seconds=1.6,
                    end_seconds=3.1,
                    duration_seconds=1.5,
                ),
            ]
        )

        assert "00:00:00,000 --> 00:00:01,250" in srt
        assert "00:00:01,600 --> 00:00:03,100" in srt
        assert "Hello world" in srt

    def test_build_mux_command_adds_video_padding_when_needed(self, tmp_path):
        service = NarratedWalkthroughService("emulator-5554", output_dir=str(tmp_path))

        cmd = service._build_mux_command(
            video_path=Path("recording.mp4"),
            audio_path=Path("narration.mp3"),
            output_path=Path("final.mp4"),
            pad_seconds=2.5,
        )

        joined = " ".join(cmd)
        assert "tpad=stop_mode=clone:stop_duration=2.500" in joined
        assert "libx264" in cmd
        assert "aac" in cmd

    def test_manifest_records_expected_artifact_paths(self, tmp_path):
        service = NarratedWalkthroughService("emulator-5554", output_dir=str(tmp_path))
        narration = service._build_timed_segments(
            [
                _SynthesizedSegment(
                    index=1,
                    title="Intro",
                    text="Hello world",
                    audio_path=tmp_path / "segment_01.mp3",
                    duration_seconds=1.0,
                    pause_after_ms=300,
                ),
                _SynthesizedSegment(
                    index=2,
                    title="End",
                    text="Done",
                    audio_path=tmp_path / "segment_02.mp3",
                    duration_seconds=2.0,
                    pause_after_ms=0,
                ),
            ]
        )
        subtitles_path = tmp_path / "walkthrough.srt"
        subtitles_path.write_text("placeholder", encoding="utf-8")
        manifest_path = service._write_manifest(
            raw_video_path=tmp_path / "recording.mp4",
            final_video_path=tmp_path / "walkthrough_narrated.mp4",
            narration=type(
                "NarrationStub",
                (),
                {
                    "audio_path": tmp_path / "narration.mp3",
                    "subtitles_path": subtitles_path,
                    "duration_seconds": 3.3,
                    "segments": narration,
                },
            )(),
            video_duration_seconds=2.7,
            video_padding_seconds=0.6,
        )

        payload = manifest_path.read_text(encoding="utf-8")
        assert "walkthrough_narrated.mp4" in payload
        assert '"video_padding_seconds": 0.6' in payload
        assert '"title": "Intro"' in payload