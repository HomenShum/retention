#!/usr/bin/env python3
"""Content Monitor Demo — shows retention.sh value for repetitive web scraping.

This agent monitors 5 URLs for content changes, summarizes diffs.
Run it 3 times to see the declining cost curve:
  - Cycle 1: Full exploration (all 5 URLs scraped + summarized)
  - Cycle 2: 3/5 URLs unchanged → retention.sh detects duplicates
  - Cycle 3: 4/5 URLs unchanged → even more savings detected

After running, check http://localhost:5173/memory?tab=analytics to see:
  - Tool call frequency (fetch_url called 15x across 3 cycles)
  - Duplicate detection (same URLs fetched repeatedly)
  - Pattern: fetch_url → summarize → store (5x per cycle)

Usage:
    pip install retention-sh requests
    python content_monitor.py
"""

import hashlib
import json
import time
from pathlib import Path

# ── retention.sh integration (2 lines) ──
from retention_sh import track, observe
track(project="content-monitor-demo")

# ── Simulated content sources ──
URLS = [
    "https://news.ycombinator.com",
    "https://techcrunch.com/category/artificial-intelligence/",
    "https://arxiv.org/list/cs.AI/recent",
    "https://github.com/trending",
    "https://www.producthunt.com",
]

_content_cache: dict[str, str] = {}


@observe(name="fetch_url")
def fetch_url(url: str) -> dict:
    """Fetch a URL and return content hash + preview."""
    # Simulate fetch — in production this would use requests
    time.sleep(0.1)  # simulate network latency
    content = f"Content from {url} at {time.strftime('%H:%M')}"
    content_hash = hashlib.md5(content.encode()).hexdigest()[:12]
    return {
        "url": url,
        "hash": content_hash,
        "preview": content[:100],
        "status": 200,
    }


@observe(name="summarize_changes")
def summarize_changes(url: str, old_hash: str, new_hash: str) -> str:
    """Summarize what changed between two content versions."""
    # Simulate LLM summarization
    time.sleep(0.05)
    if old_hash == new_hash:
        return f"No changes detected at {url}"
    return f"Content at {url} changed: hash {old_hash[:6]}→{new_hash[:6]}"


@observe(name="store_result")
def store_result(url: str, summary: str, changed: bool) -> None:
    """Store monitoring result."""
    # Simulate storage
    pass


def run_cycle(cycle_num: int) -> dict:
    """Run one monitoring cycle across all URLs."""
    print(f"\n{'='*60}")
    print(f"  Cycle {cycle_num}")
    print(f"{'='*60}")

    results = {"changed": 0, "unchanged": 0, "errors": 0}

    for url in URLS:
        # Fetch current content
        content = fetch_url(url=url)
        old_hash = _content_cache.get(url, "")
        new_hash = content["hash"]

        # Check for changes
        changed = old_hash != new_hash and old_hash != ""

        # Summarize
        summary = summarize_changes(url=url, old_hash=old_hash, new_hash=new_hash)

        # Store
        store_result(url=url, summary=summary, changed=changed)

        # Update cache
        _content_cache[url] = new_hash

        status = "CHANGED" if changed else ("NEW" if not old_hash else "unchanged")
        print(f"  [{status:>9}] {url}")

        if changed:
            results["changed"] += 1
        else:
            results["unchanged"] += 1

    return results


def main():
    print("retention.sh Content Monitor Demo")
    print("=" * 60)
    print(f"Monitoring {len(URLS)} URLs across 3 cycles")
    print(f"Events logged to: ~/.retention/activity.jsonl")
    print()

    all_results = []
    for cycle in range(1, 4):
        results = run_cycle(cycle)
        all_results.append(results)
        time.sleep(0.5)

    # Summary
    total_calls = len(URLS) * 3 * 3  # 5 URLs × 3 cycles × 3 tools per URL
    print(f"\n{'='*60}")
    print(f"  Summary")
    print(f"{'='*60}")
    print(f"  Total tool calls:     {total_calls}")
    print(f"  Unique URL patterns:  {len(URLS)}")
    print(f"  Duplicate fetches:    {len(URLS) * 2} (cycles 2+3 re-fetch same URLs)")
    print(f"  Potential savings:    {len(URLS) * 2 * 3} calls cacheable ({(len(URLS)*2*3/total_calls)*100:.0f}%)")
    print()
    print("  View your analytics at: http://localhost:5173/memory?tab=analytics")
    print("  Or run: cat ~/.retention/activity.jsonl | python -m json.tool")


if __name__ == "__main__":
    main()
