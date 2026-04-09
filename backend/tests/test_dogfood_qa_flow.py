import importlib.util
import json
from pathlib import Path


def _load_module():
    script_path = Path(__file__).resolve().parents[2] / "scripts" / "dogfood-qa-flow.py"
    spec = importlib.util.spec_from_file_location("dogfood_qa_flow", script_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class _DummyResponse:
    def __init__(self, *, status_code=200, payload=None, text=None):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text or json.dumps(self._payload)

    def json(self):
        return self._payload


def test_call_mcp_uses_tools_call_endpoint_and_unwraps_result(monkeypatch):
    module = _load_module()
    captured = {}

    def fake_post(url, headers, json, timeout):
        captured["url"] = url
        captured["headers"] = headers
        captured["json"] = json
        captured["timeout"] = timeout
        return _DummyResponse(payload={
            "tool": "retention.system_check",
            "status": "ok",
            "result": {"ready": True, "summary": "ok"},
            "error": None,
            "duration_ms": 12,
        })

    monkeypatch.setattr(module.requests, "post", fake_post)

    result = module.call_mcp("http://localhost:8000", "secret-token", "retention.system_check", {"foo": "bar"})

    assert captured["url"] == "http://localhost:8000/mcp/tools/call"
    assert captured["headers"]["Authorization"] == "Bearer secret-token"
    assert captured["json"] == {"tool": "retention.system_check", "arguments": {"foo": "bar"}}
    assert result == {"ready": True, "summary": "ok"}


def test_call_mcp_surfaces_top_level_mcp_errors(monkeypatch):
    module = _load_module()

    def fake_post(url, headers, json, timeout):
        return _DummyResponse(payload={
            "tool": "retention.system_check",
            "status": "error",
            "error": "Missing Authorization",
            "result": None,
        })

    monkeypatch.setattr(module.requests, "post", fake_post)

    result = module.call_mcp("http://localhost:8000", "", "retention.system_check", {})

    assert result == {"error": "Missing Authorization"}


def test_poll_pipeline_accepts_completed_status(monkeypatch):
    module = _load_module()
    responses = iter([
        {"status": "running", "current_stage": "crawl", "progress": {"screens": 1}},
        {"status": "completed", "current_stage": "done", "progress": {"screens": 4}},
    ])
    calls = []

    def fake_call(backend, token, tool, args):
        calls.append((backend, token, tool, args))
        return next(responses)

    monkeypatch.setattr(module, "call_mcp", fake_call)
    monkeypatch.setattr(module.time, "sleep", lambda *_args, **_kwargs: None)

    result = module.poll_pipeline("http://localhost:8000", "secret-token", "run-123", timeout=5)

    assert result["status"] == "completed"
    assert len(calls) == 2
    assert all(tool == "retention.pipeline.status" for _, _, tool, _ in calls)


def test_signup_for_token_uses_configured_identity(monkeypatch):
    module = _load_module()
    captured = {}

    def fake_post(url, json, timeout):
        captured["url"] = url
        captured["json"] = json
        captured["timeout"] = timeout
        return _DummyResponse(payload={"token": "sk-ret-test", "setup_snippet": "export RETENTION_MCP_TOKEN=..."})

    monkeypatch.setattr(module.requests, "post", fake_post)

    result = module.signup_for_token(
        "http://localhost:8000",
        "homen@retention.com",
        "Homen",
        platform="claude-code",
    )

    assert captured["url"] == "http://localhost:8000/api/signup"
    assert captured["json"] == {
        "email": "homen@retention.com",
        "name": "Homen",
        "platform": "claude-code",
    }
    assert result["token"] == "sk-ret-test"


def test_extract_failure_count_prefers_failures_list_then_summary():
    module = _load_module()

    assert module.extract_failure_count({"failures": [{}, {}]}) == 2
    assert module.extract_failure_count({"summary": {"failed": 3}}) == 3
    assert module.extract_failure_count({"failure_count": "4"}) == 4
    assert module.extract_failure_count({}) == 0


def test_run_failure_debug_loop_executes_full_fix_verify_path(monkeypatch):
    module = _load_module()
    tool_calls = []

    def fake_call(backend, token, tool, args):
        tool_calls.append((tool, args))
        responses = {
            "retention.pipeline.failure_bundle": {
                "summary": {"failed": 2},
                "failures": [{"test_id": "BUG-1"}, {"test_id": "BUG-2"}],
                "rerun_command": 'retention.pipeline.rerun_failures(baseline_run_id="run-123", failures_only=true)',
            },
            "retention.suggest_fix_context": {
                "failure_count": 2,
                "categories": ["ui_rendering"],
                "suggestions": [{"investigation": "Check button handlers"}],
            },
            "ta.feedback_package": {
                "failure_count": 2,
                "suggested_files": ["src/App.tsx"],
                "prompt": "Fix the broken button and rerun QA.",
            },
            "retention.pipeline.rerun_failures": {
                "run_id": "rerun-456",
                "status": "running",
            },
            "retention.compare_before_after": {
                "fixes": ["Delete button works"],
                "regressions": [],
                "metrics": {"fix_count": 1, "regression_count": 0},
            },
        }
        return responses[tool]

    monkeypatch.setattr(module, "call_mcp", fake_call)
    monkeypatch.setattr(
        module,
        "poll_pipeline",
        lambda backend, token, run_id, timeout: {"status": "completed", "current_stage": "done", "run_id": run_id},
    )

    result = module.run_failure_debug_loop(
        "http://localhost:8000",
        "secret-token",
        "run-123",
        "http://localhost:5173",
        rerun_timeout=5,
    )

    assert result["status"] == "completed"
    assert result["failure_count"] == 2
    assert result["rerun"]["run_id"] == "rerun-456"
    assert result["comparison"]["fixes"] == ["Delete button works"]
    assert [tool for tool, _args in tool_calls] == [
        "retention.pipeline.failure_bundle",
        "retention.suggest_fix_context",
        "ta.feedback_package",
        "retention.pipeline.rerun_failures",
        "retention.compare_before_after",
    ]


def test_run_failure_debug_loop_stops_cleanly_when_no_failures(monkeypatch):
    module = _load_module()
    tool_calls = []

    def fake_call(backend, token, tool, args):
        tool_calls.append(tool)
        return {"summary": {"failed": 0}, "failures": []}

    monkeypatch.setattr(module, "call_mcp", fake_call)

    result = module.run_failure_debug_loop(
        "http://localhost:8000",
        "secret-token",
        "run-123",
        "http://localhost:5173",
    )

    assert result["status"] == "no_failures"
    assert tool_calls == ["retention.pipeline.failure_bundle"]
