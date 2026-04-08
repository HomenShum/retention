#!/usr/bin/env python3
"""
Run calibration — generate examples from live proof data, run structured LLM judge,
compute agreement metrics.

Usage:
    cd backend && source .env
    .venv/bin/python scripts/run_calibration.py
"""

import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from dotenv import load_dotenv
load_dotenv()

from openai import OpenAI

client = OpenAI()
CAL_DIR = Path(__file__).resolve().parent.parent / "data" / "calibration"
PROOF_DIR = Path(__file__).resolve().parent.parent / "data" / "live_retention_proof"
PROMPTS_DIR = CAL_DIR / "prompts"


def load_judge_prompt(family: str) -> str:
    """Load base prompt + family-specific insert."""
    base = (PROMPTS_DIR / "base_judge_prompt.txt").read_text()
    insert_file = PROMPTS_DIR / f"{family}_judge_insert.txt"
    insert = insert_file.read_text() if insert_file.exists() else ""
    return base.replace("{FAMILY_INSERT}", insert)


def run_structured_judge(task_name: str, family: str, frontier_output: str, replay_output: str) -> dict:
    """Run the structured LLM judge with family-specific rubric."""
    prompt = load_judge_prompt(family)

    user_msg = f"""TASK: {task_name}
FAMILY: {family.upper()}

FRONTIER OUTPUT:
{frontier_output[:3000]}

REPLAY OUTPUT:
{replay_output[:3000]}"""

    start = time.time()
    response = client.chat.completions.create(
        model="gpt-5.4-mini",  # Neutral judge — not frontier, not cheap
        messages=[
            {"role": "system", "content": prompt},
            {"role": "user", "content": user_msg},
        ],
        temperature=0,
        max_completion_tokens=1000,
    )
    elapsed = time.time() - start
    usage = response.usage
    cost = (usage.prompt_tokens / 1_000_000 * 0.75) + (usage.completion_tokens / 1_000_000 * 4.50)

    output = response.choices[0].message.content

    # Parse JSON
    try:
        jm = re.search(r'\{[\s\S]*\}', output)
        parsed = json.loads(jm.group()) if jm else {}
    except (json.JSONDecodeError, AttributeError):
        parsed = {"parse_error": True, "raw": output[:500]}

    return {
        "judge_result": parsed,
        "judge_model": response.model,
        "judge_response_id": response.id,
        "judge_tokens": usage.total_tokens,
        "judge_cost_usd": round(cost, 6),
        "judge_elapsed_s": round(elapsed, 3),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def generate_examples_from_proofs() -> list:
    """Load all live retention proof results and convert to calibration examples."""
    examples = []

    for f in sorted(PROOF_DIR.glob("retention-*.json")):
        suite = json.loads(f.read_text())
        for r in suite.get("results", []):
            if "error" in r:
                continue

            example = {
                "example_id": r["task_id"],
                "workflow_family": r.get("family", ""),
                "task_name": r["task_name"],
                "task_prompt": r["phase1_frontier"].get("label", ""),
                "frontier_output": r["phase1_frontier"]["output"],
                "replay_output": r["phase3_replay"]["output"],
                "scaffold_used": r["phase2_extraction"].get("scaffold", {}),
                "frontier_cost_usd": r["comparison"]["frontier_cost_usd"],
                "replay_cost_usd": r["comparison"]["replay_cost_usd"],
                "savings_pct": r["comparison"]["savings_pct"],
                "keyword_validator_pass": r["phase3_replay"].get("correct", False),
                "source_suite": f.stem,
            }
            examples.append(example)

    return examples


def main():
    print("=" * 70)
    print("TA STUDIO — CALIBRATION RUN")
    print("Structured LLM judge with family-specific rubrics")
    print(f"Judge model: gpt-5.4-mini (neutral)")
    print(f"Timestamp: {datetime.now(timezone.utc).isoformat()}")
    print("=" * 70)

    # Generate examples
    examples = generate_examples_from_proofs()
    print(f"\nLoaded {len(examples)} examples from live retention proofs")

    # Deduplicate by example_id
    seen = set()
    unique = []
    for e in examples:
        if e["example_id"] not in seen:
            seen.add(e["example_id"])
            unique.append(e)
    examples = unique
    print(f"Unique examples: {len(examples)}")

    # Run judge on each
    results = []
    total_judge_cost = 0

    for ex in examples:
        family = ex["workflow_family"]
        task = ex["task_name"]
        eid = ex["example_id"]

        print(f"\n  [{eid}] {task} ({family})")

        judgment = run_structured_judge(
            task_name=task,
            family=family,
            frontier_output=ex["frontier_output"],
            replay_output=ex["replay_output"],
        )

        total_judge_cost += judgment["judge_cost_usd"]
        jr = judgment["judge_result"]

        verdict = jr.get("final_verdict", "?")
        confidence = jr.get("confidence", 0)
        scores = jr.get("scores", {})
        overall = scores.get("overall_quality", "?")
        pairwise = jr.get("pairwise", {})
        winner = pairwise.get("winner", "?")
        notes = jr.get("notes", "?")

        # Hard gate summary
        gates = jr.get("hard_gates", {})
        gate_fails = [k for k, v in gates.items() if v is False]
        escalation = gates.get("gate_escalation_needed", False)

        print(f"    Verdict: {verdict} (confidence={confidence})")
        print(f"    Overall: {overall}/5 | Winner: {winner} | Savings: {ex['savings_pct']}%")
        if gate_fails:
            print(f"    GATE FAILS: {gate_fails}")
        if escalation:
            print(f"    ESCALATION RECOMMENDED")
        print(f"    Notes: {str(notes)[:100]}")

        # Save example + judgment
        combined = {
            **ex,
            "llm_judge": judgment,
        }
        results.append(combined)

        # Save individual example
        ex_path = CAL_DIR / "examples" / f"{eid}.json"
        ex_path.write_text(json.dumps(ex, indent=2, default=str))

        # Save individual LLM label
        label_path = CAL_DIR / "llm_labels" / f"{eid}_llm_judge_v1.json"
        label_path.write_text(json.dumps(judgment, indent=2, default=str))

    # ── Agreement + Summary ──────────────────────────────────────
    print(f"\n{'='*70}")
    print("CALIBRATION SUMMARY")
    print(f"{'='*70}")

    verdicts = [r["llm_judge"]["judge_result"].get("final_verdict", "?") for r in results]
    from collections import Counter
    vc = Counter(verdicts)

    print(f"\n  Examples judged: {len(results)}")
    print(f"  Judge cost: ${total_judge_cost:.6f}")
    print(f"\n  Verdict distribution:")
    for v, c in vc.most_common():
        print(f"    {v:45s} {c}")

    # Keyword vs Judge agreement
    keyword_pass = sum(1 for r in results if r.get("keyword_validator_pass"))
    judge_acceptable = sum(1 for r in results if r["llm_judge"]["judge_result"].get("final_verdict", "").startswith("acceptable"))
    both_agree_good = sum(1 for r in results
        if r.get("keyword_validator_pass") and r["llm_judge"]["judge_result"].get("final_verdict", "").startswith("acceptable"))
    keyword_pass_judge_fail = sum(1 for r in results
        if r.get("keyword_validator_pass") and not r["llm_judge"]["judge_result"].get("final_verdict", "").startswith("acceptable"))

    print(f"\n  Keyword validator: {keyword_pass}/{len(results)} pass")
    print(f"  LLM judge acceptable: {judge_acceptable}/{len(results)}")
    print(f"  Both agree good: {both_agree_good}/{len(results)}")
    print(f"  Keyword pass but judge disagrees: {keyword_pass_judge_fail}/{len(results)} ← these are the dangerous ones")

    # Average scores
    all_scores = [r["llm_judge"]["judge_result"].get("scores", {}) for r in results]
    if all_scores:
        dims = ["task_success", "completeness", "faithfulness_to_frontier", "overall_quality", "safety_or_lossiness"]
        print(f"\n  Average scores:")
        for d in dims:
            vals = [s.get(d, 0) for s in all_scores if isinstance(s.get(d), (int, float))]
            if vals:
                print(f"    {d:35s} {sum(vals)/len(vals):.2f}/5")

    # Pairwise
    winners = [r["llm_judge"]["judge_result"].get("pairwise", {}).get("winner", "?") for r in results]
    wc = Counter(winners)
    print(f"\n  Pairwise winners: {dict(wc)}")

    # Escalation
    escalation_count = sum(1 for r in results
        if r["llm_judge"]["judge_result"].get("hard_gates", {}).get("gate_escalation_needed", False))
    print(f"  Escalation recommended: {escalation_count}/{len(results)}")

    # Per-family breakdown
    for family in ["csp", "drx", "qa"]:
        fam = [r for r in results if r.get("workflow_family") == family]
        if not fam:
            continue
        fam_verdicts = [r["llm_judge"]["judge_result"].get("final_verdict", "?") for r in fam]
        fam_acceptable = sum(1 for v in fam_verdicts if v.startswith("acceptable"))
        print(f"\n  {family.upper()} ({len(fam)} examples): {fam_acceptable}/{len(fam)} acceptable")
        for v, c in Counter(fam_verdicts).most_common():
            print(f"    {v}: {c}")

    # Save report
    report = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "total_examples": len(results),
        "total_judge_cost_usd": round(total_judge_cost, 6),
        "verdict_distribution": dict(vc),
        "keyword_vs_judge": {
            "keyword_pass": keyword_pass,
            "judge_acceptable": judge_acceptable,
            "both_agree_good": both_agree_good,
            "keyword_pass_judge_fail": keyword_pass_judge_fail,
        },
        "pairwise_winners": dict(wc),
        "escalation_recommended": escalation_count,
        "results": results,
    }
    report_path = CAL_DIR / "reports" / f"calibration_v1_{int(time.time())}.json"
    report_path.write_text(json.dumps(report, indent=2, default=str))
    print(f"\n  Report saved: {report_path}")
    print("=" * 70)


if __name__ == "__main__":
    main()
