#!/usr/bin/env python3
"""
LIVE END-TO-END PROOF — real API calls, real billing, real output comparison.

This script:
1. Gives the SAME coding task to a frontier model (gpt-5.4) and a cheap model (gpt-5.4-nano)
2. The frontier model reasons from scratch (discovery)
3. The cheap model follows a retained path (scaffold from the frontier run)
4. Compares: outputs, token usage, cost, latency — all from REAL API responses
5. Saves full telemetry to data/live_proof/

Usage:
    cd backend
    source .env
    .venv/bin/python scripts/live_proof.py
"""

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from dotenv import load_dotenv
load_dotenv()

from openai import OpenAI

DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "live_proof"
DATA_DIR.mkdir(parents=True, exist_ok=True)

client = OpenAI()

# ── The task: a real coding problem both models must solve ────────────

TASK = """You are a senior software engineer. Analyze this Python function and:
1. Identify the bug
2. Fix it
3. Write one test case that catches the bug
4. Explain what the fix does in one sentence

```python
def calculate_discount(price, discount_pct):
    \"\"\"Apply a percentage discount to a price.\"\"\"
    if discount_pct > 1:
        discount_pct = discount_pct / 100
    discounted = price * discount_pct
    return round(discounted, 2)

# Expected: calculate_discount(100, 20) should return 80.0
# Actual: returns 20.0
```

Respond with valid JSON:
{
    "bug_description": "...",
    "fixed_code": "def calculate_discount(price, discount_pct): ...",
    "test_case": "assert calculate_discount(100, 20) == 80.0",
    "explanation": "..."
}
"""

# ── The retained scaffold (what the frontier discovered) ─────────────
# This is what TA would provide to the cheap model after the frontier run.
# In a real system, this comes from the trajectory. Here we hard-code what
# the correct solution looks like so we can test if the cheap model follows it.

SCAFFOLD_PROMPT = """You are replaying a validated coding fix. Follow this exact scaffold:

SCAFFOLD (from prior successful run):
- Bug: The function multiplies price by discount_pct instead of (1 - discount_pct)
- Fix: Change `discounted = price * discount_pct` to `discounted = price * (1 - discount_pct)`
- Test: assert calculate_discount(100, 20) == 80.0

Apply this scaffold to produce the correct output.

```python
def calculate_discount(price, discount_pct):
    if discount_pct > 1:
        discount_pct = discount_pct / 100
    discounted = price * discount_pct
    return round(discounted, 2)
```

Respond with valid JSON:
{
    "bug_description": "...",
    "fixed_code": "def calculate_discount(price, discount_pct): ...",
    "test_case": "assert calculate_discount(100, 20) == 80.0",
    "explanation": "..."
}
"""


def run_model(model: str, prompt: str, label: str) -> dict:
    """Run a model and capture FULL telemetry from the API response."""
    print(f"\n{'='*60}")
    print(f"[{label}] Running {model}...")
    print(f"{'='*60}")

    start = time.time()
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            max_completion_tokens=2000,
        )
    except Exception as e:
        print(f"  ERROR: {e}")
        return {"error": str(e), "model": model, "label": label}

    elapsed = time.time() - start
    usage = response.usage

    # Extract all telemetry from the REAL API response
    result = {
        "label": label,
        "model": model,
        "model_actual": response.model,  # What model actually served the request
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "elapsed_s": round(elapsed, 3),

        # Token telemetry — from actual API response
        "input_tokens": usage.prompt_tokens,
        "output_tokens": usage.completion_tokens,
        "total_tokens": usage.total_tokens,

        # Cost — computed from tokens × published pricing
        "cost_usd": 0.0,  # computed below

        # Output
        "raw_output": response.choices[0].message.content,
        "finish_reason": response.choices[0].finish_reason,

        # API metadata
        "response_id": response.id,
        "system_fingerprint": getattr(response, "system_fingerprint", ""),
    }

    # Compute cost from real token counts
    pricing = {
        "gpt-5.4": {"input": 2.50, "output": 15.00},
        "gpt-5.4-mini": {"input": 0.75, "output": 4.50},
        "gpt-5.4-nano": {"input": 0.20, "output": 1.25},
        "gpt-4.1": {"input": 2.00, "output": 8.00},
        "gpt-4.1-mini": {"input": 0.40, "output": 1.60},
        "gpt-4.1-nano": {"input": 0.10, "output": 0.40},
        "gpt-4o": {"input": 2.50, "output": 10.00},
        "gpt-4o-mini": {"input": 0.15, "output": 0.60},
    }
    p = pricing.get(model, {"input": 2.50, "output": 10.00})
    result["cost_usd"] = round(
        (usage.prompt_tokens / 1_000_000 * p["input"]) +
        (usage.completion_tokens / 1_000_000 * p["output"]),
        6,
    )

    print(f"  Model served: {result['model_actual']}")
    print(f"  Tokens: in={usage.prompt_tokens} out={usage.completion_tokens} total={usage.total_tokens}")
    print(f"  Cost: ${result['cost_usd']:.6f}")
    print(f"  Latency: {elapsed:.2f}s")
    print(f"  Output preview: {result['raw_output'][:120]}...")

    return result


def validate_output(result: dict) -> dict:
    """Check if the model's output is correct."""
    output = result.get("raw_output", "")
    validation = {
        "has_json": False,
        "parsed_ok": False,
        "bug_identified": False,
        "fix_correct": False,
        "test_present": False,
        "overall_correct": False,
    }

    try:
        # Try to extract JSON
        import re
        json_match = re.search(r'\{[\s\S]*\}', output)
        if json_match:
            validation["has_json"] = True
            parsed = json.loads(json_match.group())
            validation["parsed_ok"] = True

            # Check bug identification
            bug = parsed.get("bug_description", "").lower()
            if "1 -" in bug or "subtract" in bug or "minus" in bug or "instead of" in bug:
                validation["bug_identified"] = True

            # Check fix
            fix = parsed.get("fixed_code", "")
            if "(1 -" in fix or "(1-" in fix:
                validation["fix_correct"] = True

            # Check test
            test = parsed.get("test_case", "")
            if "80" in test:
                validation["test_present"] = True

            validation["overall_correct"] = all([
                validation["bug_identified"],
                validation["fix_correct"],
                validation["test_present"],
            ])
    except (json.JSONDecodeError, AttributeError):
        pass

    return validation


def main():
    print("=" * 60)
    print("TA STUDIO — LIVE END-TO-END PROOF")
    print("Real API calls. Real billing. Real output comparison.")
    print("=" * 60)
    print(f"Timestamp: {datetime.now(timezone.utc).isoformat()}")
    print()

    # ── Run 1: Frontier model (full reasoning, no scaffold) ──────────
    frontier = run_model("gpt-5.4", TASK, "FRONTIER (discovery)")
    frontier_valid = validate_output(frontier)
    frontier["validation"] = frontier_valid

    # ── Run 2: Same frontier, with scaffold (retained path) ──────────
    retained = run_model("gpt-5.4", SCAFFOLD_PROMPT, "RETAINED (same model, scaffold)")
    retained_valid = validate_output(retained)
    retained["validation"] = retained_valid

    # ── Run 3: Cheap model with scaffold ─────────────────────────────
    cheap = run_model("gpt-5.4-nano", SCAFFOLD_PROMPT, "CHEAP REPLAY (nano + scaffold)")
    cheap_valid = validate_output(cheap)
    cheap["validation"] = cheap_valid

    # ── Run 4: Mid-tier model with scaffold ──────────────────────────
    mid = run_model("gpt-5.4-mini", SCAFFOLD_PROMPT, "MID REPLAY (mini + scaffold)")
    mid_valid = validate_output(mid)
    mid["validation"] = mid_valid

    # ── Comparison ───────────────────────────────────────────────────
    runs = [frontier, retained, cheap, mid]

    print("\n" + "=" * 60)
    print("RESULTS COMPARISON")
    print("=" * 60)

    header = "{:35s} {:>8s} {:>8s} {:>10s} {:>8s} {:>8s}".format(
        "Run", "InTok", "OutTok", "Cost", "Time", "Correct")
    print(header)
    print("-" * 85)

    for r in runs:
        if "error" in r:
            print(f"  {r['label']:35s} ERROR: {r['error'][:40]}")
            continue
        v = r.get("validation", {})
        correct = "YES" if v.get("overall_correct") else "NO"
        print("{:35s} {:>8d} {:>8d} ${:>9.6f} {:>7.2f}s {:>8s}".format(
            r["label"][:35],
            r.get("input_tokens", 0),
            r.get("output_tokens", 0),
            r.get("cost_usd", 0),
            r.get("elapsed_s", 0),
            correct,
        ))

    # ── Savings calculation ──────────────────────────────────────────
    if not frontier.get("error") and not cheap.get("error"):
        fc = frontier["cost_usd"]
        cc = cheap["cost_usd"]
        savings = (1 - cc / fc) * 100 if fc > 0 else 0

        print()
        print("=" * 60)
        print("SAVINGS PROOF")
        print("=" * 60)
        print(f"  Frontier cost:   ${fc:.6f} ({frontier['model']})")
        print(f"  Cheap cost:      ${cc:.6f} ({cheap['model']})")
        print(f"  Savings:         {savings:.1f}%")
        print(f"  Frontier correct: {frontier_valid.get('overall_correct')}")
        print(f"  Cheap correct:    {cheap_valid.get('overall_correct')}")
        print(f"  Outcome equivalent: {frontier_valid.get('overall_correct') == cheap_valid.get('overall_correct')}")

    # ── Save full telemetry ──────────────────────────────────────────
    proof_id = f"proof-{int(time.time())}"
    proof = {
        "proof_id": proof_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "task": "calculate_discount bug fix",
        "runs": runs,
        "comparison": {
            "frontier_cost": frontier.get("cost_usd", 0),
            "cheap_cost": cheap.get("cost_usd", 0),
            "savings_pct": round(savings, 1) if not frontier.get("error") and not cheap.get("error") else 0,
            "frontier_correct": frontier_valid.get("overall_correct", False),
            "cheap_correct": cheap_valid.get("overall_correct", False),
            "outcome_equivalent": frontier_valid.get("overall_correct") == cheap_valid.get("overall_correct"),
        },
    }

    proof_path = DATA_DIR / f"{proof_id}.json"
    proof_path.write_text(json.dumps(proof, indent=2, default=str))
    print(f"\n  Full telemetry saved: {proof_path}")
    print("=" * 60)


if __name__ == "__main__":
    main()
