import json

from app.agents.orchestration import AgenticUpdateEvaluator


class _DummyResponses:
    def __init__(self, output_text: str):
        self._output_text = output_text

    def create(self, **kwargs):
        return type("DummyResponse", (), {"output_text": self._output_text})()


class _DummyClient:
    def __init__(self, output_text: str):
        self.responses = _DummyResponses(output_text)


def test_static_checks_pass_for_latest_agentic_update(tmp_path):
    evaluator = AgenticUpdateEvaluator(runs_dir=tmp_path, api_key="")

    report = evaluator.run_static_checks()

    assert report["passed"] is True
    assert any(check["name"] == "alias_activation_contract" for check in report["checks"])
    assert any(check["name"] == "qa_emulation_direct_context_loads" for check in report["checks"])


def test_build_report_skips_live_judge_without_api_key(tmp_path):
    evaluator = AgenticUpdateEvaluator(runs_dir=tmp_path, api_key="")

    report = evaluator.build_report()

    assert report["static_evaluation"]["passed"] is True
    assert report["live_judge"]["skipped"] is True
    assert report["overall_passed"] is True


def test_persist_report_writes_json_artifact(tmp_path):
    evaluator = AgenticUpdateEvaluator(runs_dir=tmp_path, api_key="")

    report = evaluator.build_report()
    artifact_path = evaluator.persist_report(report)

    assert artifact_path is not None
    assert artifact_path.exists()
    payload = json.loads(artifact_path.read_text(encoding="utf-8"))
    assert payload["run_id"] == report["run_id"]
    assert payload["target_update"] == evaluator.TARGET_UPDATE


def test_live_judge_uses_mocked_client_when_configured(tmp_path):
    output_text = json.dumps(
        {
            "passed": True,
            "confidence": 0.93,
            "reasoning": "Deterministic evidence shows the integration is coherent.",
            "issues": [],
            "suggestions": ["Keep the evaluation focused on repo-native wiring."],
        }
    )
    evaluator = AgenticUpdateEvaluator(
        runs_dir=tmp_path,
        api_key="test-key",
        client=_DummyClient(output_text),
    )

    report = evaluator.build_report()

    assert report["live_judge"]["skipped"] is False
    assert report["live_judge"]["judgment"]["passed"] is True
    assert report["overall_passed"] is True