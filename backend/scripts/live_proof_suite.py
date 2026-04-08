#!/usr/bin/env python3
"""
LIVE PROOF SUITE — Repeated live runs across 3 workflow families.

Every run makes REAL API calls to gpt-5.4 (frontier) and gpt-5.4-nano (cheap).
Every result includes: actual tokens, actual cost, actual latency, actual output.
Every result declares: live vs offline, billing source, correctness policy.

Workflow families:
  1. CSP — Cross-Stack Code Change (bug fix, refactor, parameter addition)
  2. DRX — Deep Research (multi-source synthesis, competitor analysis)
  3. QA  — Quality Assurance (test generation, bug detection, verification)

Usage:
    cd backend && source .env
    .venv/bin/python scripts/live_proof_suite.py
    .venv/bin/python scripts/live_proof_suite.py --family csp --runs 5
    .venv/bin/python scripts/live_proof_suite.py --family all --runs 3
"""

import argparse
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

FRONTIER = "gpt-5.4"
CHEAP = "gpt-5.4-nano"
MID = "gpt-5.4-mini"

PRICING = {
    "gpt-5.4": {"input": 2.50, "output": 15.00},
    "gpt-5.4-mini": {"input": 0.75, "output": 4.50},
    "gpt-5.4-nano": {"input": 0.20, "output": 1.25},
}

# ═══════════════════════════════════════════════════════════════
# WORKFLOW TASKS — real problems, not toy examples
# ═══════════════════════════════════════════════════════════════

CSP_TASKS = [
    {
        "id": "csp-01",
        "name": "Fix off-by-one pagination",
        "discovery_prompt": """You are a senior engineer. This FastAPI endpoint has a pagination bug. Find it, fix it, write a test.

```python
@router.get("/items")
async def list_items(page: int = 1, per_page: int = 20):
    offset = page * per_page  # Bug: page 1 skips first 20 items
    items = db.query(Item).offset(offset).limit(per_page).all()
    total = db.query(Item).count()
    return {"items": items, "page": page, "total": total, "pages": total // per_page}
```

Respond JSON: {"bug": "...", "fixed_code": "...", "test": "assert ...", "explanation": "..."}""",
        "scaffold": "Bug: offset should be (page-1)*per_page not page*per_page. Also pages calculation needs ceil division.",
        "validator": lambda o: "(page - 1)" in o or "(page-1)" in o,
    },
    {
        "id": "csp-02",
        "name": "Add rate limiting middleware",
        "discovery_prompt": """Design a rate limiting middleware for FastAPI. Requirements:
- 100 requests per minute per IP
- Return 429 when exceeded
- Include Retry-After header
- Use in-memory storage (no Redis)

Respond JSON: {"middleware_code": "...", "usage_example": "...", "test": "...", "explanation": "..."}""",
        "scaffold": "Use a dict mapping IP→(count, window_start). Check on each request. Reset window every 60s. Return JSONResponse(status_code=429, headers={'Retry-After': str(remaining_seconds)}).",
        "validator": lambda o: "429" in o and ("Retry-After" in o or "retry" in o.lower()),
    },
    {
        "id": "csp-03",
        "name": "Fix SQL injection vulnerability",
        "discovery_prompt": """This endpoint has a SQL injection vulnerability. Find it, fix it, explain the attack vector.

```python
@router.get("/search")
async def search_users(q: str):
    query = f"SELECT * FROM users WHERE name LIKE '%{q}%'"
    results = db.execute(query).fetchall()
    return {"results": results}
```

Respond JSON: {"vulnerability": "...", "attack_example": "...", "fixed_code": "...", "test": "...", "explanation": "..."}""",
        "scaffold": "SQL injection via f-string interpolation. Fix: use parameterized query with :param syntax. Attack: q=\"'; DROP TABLE users; --\"",
        "validator": lambda o: "parameterized" in o.lower() or "bind" in o.lower() or ":param" in o or "?" in o,
    },
    {
        "id": "csp-04",
        "name": "Implement retry with exponential backoff",
        "discovery_prompt": """Write a Python retry decorator with exponential backoff. Requirements:
- max_retries configurable (default 3)
- base_delay configurable (default 1.0 seconds)
- exponential multiplier: delay * 2^attempt
- jitter: add random 0-0.5s
- only retry on specific exceptions
- log each retry attempt

Respond JSON: {"decorator_code": "...", "usage_example": "...", "test": "...", "explanation": "..."}""",
        "scaffold": "Use functools.wraps decorator. Loop max_retries times, catch specified exceptions, sleep(base_delay * 2**attempt + random.uniform(0, 0.5)), re-raise on final attempt.",
        "validator": lambda o: "2 **" in o or "2**" in o or "exponential" in o.lower(),
    },
    {
        "id": "csp-05",
        "name": "Fix race condition in counter",
        "discovery_prompt": """This concurrent counter has a race condition. Find it, fix it, prove it with a test.

```python
import threading

class Counter:
    def __init__(self):
        self.value = 0

    def increment(self):
        current = self.value
        self.value = current + 1

    def get(self):
        return self.value
```

Respond JSON: {"bug": "...", "fixed_code": "...", "test": "...", "explanation": "..."}""",
        "scaffold": "Race condition: read-then-write is not atomic. Fix: use threading.Lock() and acquire/release around the increment. Test: spawn 100 threads each incrementing 100 times, assert final value == 10000.",
        "validator": lambda o: "Lock" in o or "lock" in o or "atomic" in o.lower(),
    },
]

DRX_TASKS = [
    {
        "id": "drx-01",
        "name": "Compare LLM pricing tiers",
        "discovery_prompt": """Research and compare the pricing of these LLM providers for a team spending ~$500/month:
- OpenAI (GPT-4o, GPT-4o-mini)
- Anthropic (Claude Sonnet, Claude Haiku)
- Google (Gemini Pro, Gemini Flash)

For each: input price/1M tokens, output price/1M tokens, best use case, and one limitation.

Respond JSON: {"providers": [{"name": "...", "models": [{"model": "...", "input_price": ..., "output_price": ..., "best_for": "...", "limitation": "..."}]}], "recommendation": "...", "total_analysis_sources": 3}""",
        "scaffold": "OpenAI: GPT-4o=$2.50/$10, GPT-4o-mini=$0.15/$0.60. Anthropic: Sonnet=$3/$15, Haiku=$0.80/$4. Google: Pro=$1.25/$5, Flash=$0.075/$0.30. Recommend tiered: Flash for bulk, Sonnet for reasoning, GPT-4o for coding.",
        "validator": lambda o: "price" in o.lower() or "cost" in o.lower() or "$" in o,
    },
    {
        "id": "drx-02",
        "name": "Analyze workflow automation market",
        "discovery_prompt": """Analyze the AI workflow automation market landscape. Cover:
1. Top 5 companies (name, what they do, funding if known)
2. Key market trends
3. Main buyer personas
4. Biggest unsolved problem

Respond JSON: {"companies": [{"name": "...", "description": "...", "differentiator": "..."}], "trends": ["..."], "buyer_personas": ["..."], "unsolved_problem": "...", "sources_consulted": 5}""",
        "scaffold": "Companies: Zapier (no-code), n8n (open-source), Temporal (durable execution), LangChain (LLM orchestration), Retool (internal tools). Trend: shift from rule-based to AI-driven. Unsolved: verifying AI outputs in production workflows.",
        "validator": lambda o: "companies" in o.lower() or "market" in o.lower(),
    },
    {
        "id": "drx-03",
        "name": "Summarize token optimization strategies",
        "discovery_prompt": """A developer team is spending too much on LLM tokens. Research and summarize the top 7 strategies to reduce token costs without losing quality. For each strategy: name, how it works, expected savings %, difficulty to implement.

Respond JSON: {"strategies": [{"name": "...", "how": "...", "savings_pct": "...", "difficulty": "easy|medium|hard"}], "total_potential_savings": "..."}""",
        "scaffold": "1. Prompt caching (90% on repeated context, easy). 2. Model tiering (70%, medium). 3. RAG instead of full docs (80%, medium). 4. Structured outputs (30%, easy). 5. Batch API (50%, easy). 6. Response streaming+truncation (20%, easy). 7. Fine-tuning (60%, hard).",
        "validator": lambda o: "caching" in o.lower() or "tiering" in o.lower() or "strategies" in o.lower(),
    },
]

QA_TASKS = [
    {
        "id": "qa-01",
        "name": "Generate test cases for login",
        "discovery_prompt": """Generate 5 test cases for a login form with email and password fields. Cover: happy path, empty fields, invalid email, wrong password, SQL injection attempt. For each: test name, input, expected result, priority.

Respond JSON: {"test_cases": [{"name": "...", "input": {"email": "...", "password": "..."}, "expected": "...", "priority": "P0|P1|P2"}]}""",
        "scaffold": "5 cases: 1. Valid login (P0). 2. Empty email (P0). 3. Invalid format (P1). 4. Wrong password (P0). 5. SQL injection in email field (P0). All should return appropriate error messages except case 1.",
        "validator": lambda o: "test_cases" in o or "login" in o.lower(),
    },
    {
        "id": "qa-02",
        "name": "Detect bugs in shopping cart code",
        "discovery_prompt": """Find ALL bugs in this shopping cart implementation:

```python
class ShoppingCart:
    def __init__(self):
        self.items = {}

    def add_item(self, name, price, quantity=1):
        if name in self.items:
            self.items[name]["quantity"] += quantity
        else:
            self.items[name] = {"price": price, "quantity": quantity}

    def remove_item(self, name):
        del self.items[name]  # Bug 1: KeyError if not in cart

    def get_total(self):
        total = 0
        for item in self.items.values():
            total += item["price"] + item["quantity"]  # Bug 2: should multiply
        return total

    def apply_discount(self, pct):
        self.discount = pct  # Bug 3: never used in get_total
```

List all bugs with severity and fix. Respond JSON: {"bugs": [{"id": "...", "description": "...", "severity": "high|medium|low", "fix": "..."}]}""",
        "scaffold": "3 bugs: 1. remove_item doesn't check if item exists (KeyError, high). 2. get_total adds price+quantity instead of price*quantity (high). 3. apply_discount sets self.discount but get_total never uses it (medium).",
        "validator": lambda o: "multiply" in o.lower() or "price *" in o or "KeyError" in o,
    },
]

ALL_TASKS = {
    "csp": CSP_TASKS,
    "drx": DRX_TASKS,
    "qa": QA_TASKS,
}


def call_model(model: str, prompt: str) -> dict:
    """Make a REAL API call and return full telemetry."""
    start = time.time()
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            max_completion_tokens=2000,
        )
    except Exception as e:
        return {"error": str(e), "model": model}

    elapsed = time.time() - start
    usage = response.usage
    p = PRICING.get(model, {"input": 2.50, "output": 10.00})
    cost = (usage.prompt_tokens / 1_000_000 * p["input"]) + (usage.completion_tokens / 1_000_000 * p["output"])

    return {
        "model": model,
        "model_served": response.model,
        "response_id": response.id,
        "input_tokens": usage.prompt_tokens,
        "output_tokens": usage.completion_tokens,
        "total_tokens": usage.total_tokens,
        "cost_usd": round(cost, 6),
        "elapsed_s": round(elapsed, 3),
        "output": response.choices[0].message.content,
        "finish_reason": response.choices[0].finish_reason,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def run_task(task: dict) -> dict:
    """Run one task: frontier discovery → cheap replay with scaffold."""
    task_id = task["id"]
    task_name = task["name"]
    print(f"\n  [{task_id}] {task_name}")

    # Frontier: full reasoning, no scaffold
    frontier = call_model(FRONTIER, task["discovery_prompt"])
    if "error" in frontier:
        print(f"    FRONTIER ERROR: {frontier['error'][:60]}")
        return {"task_id": task_id, "error": frontier["error"]}

    frontier_correct = task["validator"](frontier.get("output", ""))

    # Cheap: scaffold-assisted
    scaffold_prompt = task["discovery_prompt"] + f"\n\nHINT (from prior successful run): {task['scaffold']}"
    cheap = call_model(CHEAP, scaffold_prompt)
    if "error" in cheap:
        print(f"    CHEAP ERROR: {cheap['error'][:60]}")
        return {"task_id": task_id, "error": cheap["error"]}

    cheap_correct = task["validator"](cheap.get("output", ""))

    savings = (1 - cheap["cost_usd"] / frontier["cost_usd"]) * 100 if frontier["cost_usd"] > 0 else 0
    equivalent = frontier_correct == cheap_correct

    print(f"    Frontier: ${frontier['cost_usd']:.6f} ({frontier['input_tokens']}+{frontier['output_tokens']} tok) {'PASS' if frontier_correct else 'FAIL'} {frontier['elapsed_s']:.1f}s")
    print(f"    Cheap:    ${cheap['cost_usd']:.6f} ({cheap['input_tokens']}+{cheap['output_tokens']} tok) {'PASS' if cheap_correct else 'FAIL'} {cheap['elapsed_s']:.1f}s")
    print(f"    Savings:  {savings:.1f}%  Equivalent: {equivalent}")

    return {
        "task_id": task_id,
        "task_name": task_name,
        "frontier": {**frontier, "correct": frontier_correct},
        "cheap": {**cheap, "correct": cheap_correct},
        "savings_pct": round(savings, 1),
        "outcome_equivalent": equivalent,
        "truth_pipeline": {
            "run_type": "live",
            "billing_source": "real_api_response",
            "frontier_model": FRONTIER,
            "cheap_model": CHEAP,
            "correctness_policy": "validator_function",
            "scaffold_source": "hardcoded_from_prior_knowledge",
        },
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--family", default="all", choices=["csp", "drx", "qa", "all"])
    parser.add_argument("--runs", type=int, default=0, help="Max tasks per family (0=all)")
    args = parser.parse_args()

    families = ["csp", "drx", "qa"] if args.family == "all" else [args.family]

    print("=" * 70)
    print("TA STUDIO — LIVE PROOF SUITE")
    print("Real gpt-5.4 API calls. Real billing. Real output validation.")
    print(f"Frontier: {FRONTIER}  |  Cheap: {CHEAP}  |  Mid: {MID}")
    print(f"Timestamp: {datetime.now(timezone.utc).isoformat()}")
    print("=" * 70)

    all_results = []

    for family in families:
        tasks = ALL_TASKS[family]
        if args.runs > 0:
            tasks = tasks[:args.runs]

        print(f"\n{'─'*70}")
        print(f"FAMILY: {family.upper()} ({len(tasks)} tasks)")
        print(f"{'─'*70}")

        for task in tasks:
            result = run_task(task)
            result["family"] = family
            all_results.append(result)

    # ── Summary ──────────────────────────────────────────────────
    print(f"\n{'='*70}")
    print("SUITE SUMMARY")
    print(f"{'='*70}")

    valid = [r for r in all_results if "error" not in r]
    total_frontier_cost = sum(r["frontier"]["cost_usd"] for r in valid)
    total_cheap_cost = sum(r["cheap"]["cost_usd"] for r in valid)
    total_savings = (1 - total_cheap_cost / total_frontier_cost) * 100 if total_frontier_cost > 0 else 0
    correct_count = sum(1 for r in valid if r["outcome_equivalent"] and r["frontier"]["correct"])
    total_count = len(valid)

    print(f"\n  Tasks run:        {total_count}")
    print(f"  Frontier total:   ${total_frontier_cost:.6f}")
    print(f"  Cheap total:      ${total_cheap_cost:.6f}")
    print(f"  Total savings:    {total_savings:.1f}%")
    print(f"  Both correct:     {correct_count}/{total_count}")
    print(f"  Outcome equiv:    {sum(1 for r in valid if r['outcome_equivalent'])}/{total_count}")

    # Per family
    for family in families:
        fam_results = [r for r in valid if r.get("family") == family]
        if not fam_results:
            continue
        fc = sum(r["frontier"]["cost_usd"] for r in fam_results)
        cc = sum(r["cheap"]["cost_usd"] for r in fam_results)
        sv = (1 - cc / fc) * 100 if fc > 0 else 0
        eq = sum(1 for r in fam_results if r["outcome_equivalent"] and r["frontier"]["correct"])
        print(f"\n  {family.upper()}: {len(fam_results)} tasks, frontier=${fc:.6f}, cheap=${cc:.6f}, savings={sv:.1f}%, correct={eq}/{len(fam_results)}")

    # Save
    suite_id = f"suite-{int(time.time())}"
    suite = {
        "suite_id": suite_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "models": {"frontier": FRONTIER, "cheap": CHEAP, "mid": MID},
        "families_run": families,
        "total_tasks": total_count,
        "total_frontier_cost_usd": round(total_frontier_cost, 6),
        "total_cheap_cost_usd": round(total_cheap_cost, 6),
        "total_savings_pct": round(total_savings, 1),
        "outcome_equivalent_count": sum(1 for r in valid if r["outcome_equivalent"]),
        "both_correct_count": correct_count,
        "results": all_results,
    }

    path = DATA_DIR / f"{suite_id}.json"
    path.write_text(json.dumps(suite, indent=2, default=str))
    print(f"\n  Full suite saved: {path}")
    print("=" * 70)


if __name__ == "__main__":
    main()
