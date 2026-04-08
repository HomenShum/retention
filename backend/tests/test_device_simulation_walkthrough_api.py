"""Targeted API tests for narrated walkthrough generation."""

from pathlib import Path
import sys

from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.api import device_simulation as device_simulation_router


class _FakeWalkthroughResult:
    def __init__(self, screenshots_root: Path) -> None:
        base = screenshots_root / "demo_walkthroughs" / "test-session"
        self._payload = {
            "session_id": "test-session",
            "output_dir": str(base),
            "raw_video_path": str(base / "recording.mp4"),
            "final_video_path": str(base / "walkthrough_narrated.mp4"),
            "narration_audio_path": str(base / "audio" / "narration.mp3"),
            "subtitles_path": str(base / "walkthrough.srt"),
            "manifest_path": str(base / "walkthrough_manifest.json"),
            "video_duration_seconds": 8.2,
            "narration_duration_seconds": 7.7,
            "video_padding_seconds": 0.5,
            "segments": [
                {"title": "Intro", "text": "Hello world"},
                {"title": "Wrap Up", "text": "Done"},
            ],
        }

    def to_dict(self):
        return self._payload


class _FakeWalkthroughService:
    init_calls = []
    generate_calls = []

    def __init__(self, device_id: str, model: str = "tts-1", voice: str = "alloy", **_: object) -> None:
        self.__class__.init_calls.append(
            {"device_id": device_id, "model": model, "voice": voice}
        )

    async def generate_walkthrough(self, **kwargs: object):
        self.__class__.generate_calls.append(kwargs)
        screenshots_root = Path(__file__).resolve().parents[1] / "screenshots"
        return _FakeWalkthroughResult(screenshots_root)


def _build_client() -> TestClient:
    app = FastAPI()
    app.include_router(device_simulation_router.router)
    return TestClient(app)


def test_narrated_walkthrough_endpoint_returns_static_artifact_urls(monkeypatch) -> None:
    _FakeWalkthroughService.init_calls.clear()
    _FakeWalkthroughService.generate_calls.clear()
    monkeypatch.setattr(
        device_simulation_router,
        "NarratedWalkthroughService",
        _FakeWalkthroughService,
    )

    with _build_client() as client:
        response = client.post(
            "/api/device-simulation/walkthroughs/narrated",
            json={
                "device_id": "emulator-5554",
                "duration": 24,
                "script": "Intro: Hello world\n\nWrap Up: Done",
                "model": "tts-1",
                "voice": "alloy",
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["segments_count"] == 2
    assert payload["artifact_urls"]["final_video"] == (
        "/static/screenshots/demo_walkthroughs/test-session/walkthrough_narrated.mp4"
    )
    assert _FakeWalkthroughService.init_calls[0]["device_id"] == "emulator-5554"
    assert _FakeWalkthroughService.generate_calls[0]["duration"] == 24
    assert _FakeWalkthroughService.generate_calls[0]["segments"][0]["title"] == "Intro"


def test_narrated_walkthrough_endpoint_rejects_blank_script(monkeypatch) -> None:
    _FakeWalkthroughService.generate_calls.clear()
    monkeypatch.setattr(
        device_simulation_router,
        "NarratedWalkthroughService",
        _FakeWalkthroughService,
    )

    with _build_client() as client:
        response = client.post(
            "/api/device-simulation/walkthroughs/narrated",
            json={"device_id": "emulator-5554", "script": "   "},
        )

    assert response.status_code == 422
    assert "non-empty walkthrough script" in response.json()["detail"]
    assert _FakeWalkthroughService.generate_calls == []