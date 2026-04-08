#!/usr/bin/env python3
"""
Agent Chat Eval Runner — Tests all prompts in the corpus and reports pass rate.
Flywheel: successful real-user prompts get added to the corpus automatically.

Usage:
  python run_agent_eval.py                    # Run all
  python run_agent_eval.py --category qa      # Run one category
  python run_agent_eval.py --add "My prompt"  # Add a prompt to corpus
"""

import json
import time
import sys
import httpx
import argparse
from pathlib import Path
from datetime import datetime

CORPUS_PATH = Path(__file__).resolve().parents[1] / "data" / "eval" / "agent_chat_corpus.json"
API_BASE = "http://localhost:8000"


def load_corpus():
    with open(CORPUS_PATH) as f:
        return json.load(f)


def save_corpus(corpus):
    with open(CORPUS_PATH, "w") as f:
        json.dump(corpus, f, indent=2)


def test_prompt(prompt_data: dict) -> dict:
    """Test a single prompt against the agent chat endpoint."""
    prompt = prompt_data["prompt"]
    timeout = prompt_data.get("timeout_s", 60)
    min_length = prompt_data.get("min_response_length", 20)

    start = time.time()
    try:
        response = httpx.post(
            f"{API_BASE}/api/ai-agent/chat/stream",
            json={"messages": [{"role": "user", "content": prompt}]},
            timeout=timeout,
        )
        elapsed = time.time() - start
        body = response.text

        # Parse SSE events
        content = ""
        tool_calls = []
        errors = []
        for line in body.split("\n"):
            if line.startswith("data: "):
                try:
                    data = json.loads(line[6:])
                    if "content" in data and isinstance(data["content"], str):
                        content = data["content"]
                    if "tool_name" in data:
                        tool_calls.append(data["tool_name"])
                    if "error" in data:
                        errors.append(data["error"])
                except json.JSONDecodeError:
                    pass

        passed = len(content) >= min_length and len(errors) == 0
        return {
            "id": prompt_data["id"],
            "prompt": prompt,
            "passed": passed,
            "content_length": len(content),
            "tool_calls": list(set(tool_calls)),
            "errors": errors,
            "elapsed_s": round(elapsed, 1),
            "content_preview": content[:150] if content else "(empty)",
        }
    except Exception as e:
        elapsed = time.time() - start
        return {
            "id": prompt_data["id"],
            "prompt": prompt,
            "passed": False,
            "content_length": 0,
            "tool_calls": [],
            "errors": [str(e)],
            "elapsed_s": round(elapsed, 1),
            "content_preview": f"ERROR: {e}",
        }


def run_eval(category_filter=None):
    """Run all prompts and report results."""
    corpus = load_corpus()
    results = []
    total = 0
    passed = 0

    for cat_key, cat in corpus["categories"].items():
        if category_filter and cat_key != category_filter:
            continue

        print(f"\n{'='*60}")
        print(f"Category: {cat_key} — {cat['description']}")
        print(f"{'='*60}")

        for prompt_data in cat["prompts"]:
            if prompt_data.get("requires_prior"):
                print(f"  ⏭️  SKIP: {prompt_data['id']} (requires prior context)")
                continue

            total += 1
            result = test_prompt(prompt_data)
            results.append(result)

            if result["passed"]:
                passed += 1
                print(f"  ✅ {result['id']}: {result['elapsed_s']}s, {result['content_length']} chars, tools: {result['tool_calls']}")
            else:
                print(f"  ❌ {result['id']}: {result['elapsed_s']}s — {result['content_preview'][:80]}")

    # Summary
    rate = (passed / total * 100) if total > 0 else 0
    print(f"\n{'='*60}")
    print(f"RESULTS: {passed}/{total} passed ({rate:.0f}%)")
    print(f"{'='*60}")

    # Save run
    run = {
        "timestamp": datetime.now().isoformat(),
        "total": total,
        "passed": passed,
        "rate": round(rate, 1),
        "results": results,
    }
    corpus["runs"].append(run)

    # Keep only last 20 runs
    corpus["runs"] = corpus["runs"][-20:]
    save_corpus(corpus)

    return rate


def add_prompt(prompt_text: str, category: str = "user_added"):
    """Flywheel: add a new prompt to the corpus."""
    corpus = load_corpus()
    if category not in corpus["categories"]:
        corpus["categories"][category] = {
            "description": "User-contributed prompts (flywheel)",
            "prompts": [],
        }

    existing_ids = set()
    for cat in corpus["categories"].values():
        for p in cat["prompts"]:
            existing_ids.add(p["id"])

    new_id = f"user-{len(existing_ids)+1:03d}"
    corpus["categories"][category]["prompts"].append({
        "id": new_id,
        "prompt": prompt_text,
        "expected_tools": [],
        "min_response_length": 20,
        "pass_criteria": "produces meaningful response",
        "timeout_s": 90,
    })

    # Log growth
    corpus["flywheel"]["growth_log"].append({
        "timestamp": datetime.now().isoformat(),
        "prompt": prompt_text,
        "id": new_id,
    })

    save_corpus(corpus)
    print(f"Added: {new_id} → \"{prompt_text}\"")
    print(f"Corpus now has {sum(len(c['prompts']) for c in corpus['categories'].values())} prompts")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Agent Chat Eval Runner")
    parser.add_argument("--category", "-c", help="Run only this category")
    parser.add_argument("--add", "-a", help="Add a prompt to the corpus")
    parser.add_argument("--add-category", default="user_added", help="Category for --add")
    args = parser.parse_args()

    if args.add:
        add_prompt(args.add, args.add_category)
    else:
        run_eval(args.category)
