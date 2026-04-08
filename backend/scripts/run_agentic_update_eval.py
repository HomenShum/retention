import sys
from pathlib import Path

from dotenv import load_dotenv

# Add backend to path
backend_path = Path(__file__).parent.parent
sys.path.append(str(backend_path))

load_dotenv()

from app.agents.orchestration import AgenticUpdateEvaluator


def main() -> int:
    print("🔎 Running autoresearch-style eval for the latest agentic update...")
    evaluator = AgenticUpdateEvaluator()
    report, artifact_path = evaluator.run_and_persist()

    static_eval = report["static_evaluation"]
    print(f"Target update: {report['target_update']}")
    print(f"Static evaluation passed: {static_eval['passed']}")
    for check in static_eval["checks"]:
        status = "PASS" if check["passed"] else "FAIL"
        print(f" - [{status}] {check['name']}: {check['details']}")

    live_judge = report["live_judge"]
    if live_judge.get("skipped"):
        print(f"Live judge: skipped ({live_judge.get('reason')})")
    else:
        judgment = live_judge.get("judgment", {})
        print(
            "Live judge: "
            f"passed={judgment.get('passed')} confidence={judgment.get('confidence')} "
            f"model={live_judge.get('model')}"
        )
        print(f"Reasoning: {judgment.get('reasoning')}")

    if artifact_path:
        print(f"Artifact written to: {artifact_path}")
    else:
        print("Artifact persistence failed.")

    return 0 if report["overall_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())