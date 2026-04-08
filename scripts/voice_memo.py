#!/usr/bin/env python3
"""Generate a short spoken-status memo from structured inputs.

Usage examples:
  python3 scripts/voice_memo.py \
    --headline "Retention installer is live" \
    --what-happened "The public install.sh endpoint is serving a bash script again." \
    --why-it-matters "That keeps the one-command setup door open for new users." \
    --next-step "Run the installer from a clean terminal and confirm retention appears in /mcp."

  echo '{"headline":"Sprint check-in","what_happened":"Build passed","why_it_matters":"We can ship","next_step":"Merge after review"}' \
    | python3 scripts/voice_memo.py --stdin-json
"""

import argparse
import json
import sys
from typing import Any, Dict, List, Optional


def _clean(text: str) -> str:
    text = " ".join((text or "").strip().split())
    if not text:
        return ""
    if text[-1] not in ".!?":
        text += "."
    return text


def _read_json(path: Optional[str], use_stdin: bool) -> Dict[str, Any]:
    if use_stdin:
        raw = sys.stdin.read().strip()
        return json.loads(raw) if raw else {}
    if path:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    return {}


def _pick(data: Dict[str, Any], *keys: str, default: str = "") -> str:
    for key in keys:
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return default


def build_memo(
    headline: str,
    what_happened: str,
    why_it_matters: str,
    next_step: str,
    evidence: Optional[List[str]] = None,
) -> str:
    evidence = [item.strip() for item in (evidence or []) if item and item.strip()]

    parts = ["Quick voice memo."]
    if headline:
        parts.append(_clean(headline))
    if what_happened:
        parts.append(_clean("What happened: %s" % what_happened))
    if why_it_matters:
        parts.append(_clean("Why it matters: %s" % why_it_matters))
    if next_step:
        parts.append(_clean("Next step: %s" % next_step))
    if evidence:
        joined = "; ".join(evidence[:3])
        parts.append(_clean("Evidence: %s" % joined))
    return " ".join(part for part in parts if part)


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate a concise voice-style status memo")
    parser.add_argument("--headline", default="", help="Top-line status in plain English")
    parser.add_argument("--what-happened", default="", help="What changed or what was verified")
    parser.add_argument("--why-it-matters", default="", help="Why the listener should care")
    parser.add_argument("--next-step", default="", help="Immediate next action")
    parser.add_argument("--evidence", action="append", default=[], help="Optional proof points (repeatable)")
    parser.add_argument("--json", default="", help="Path to JSON file with the same fields")
    parser.add_argument("--stdin-json", action="store_true", help="Read JSON payload from stdin")
    args = parser.parse_args()

    payload = _read_json(args.json or None, args.stdin_json)

    headline = args.headline or _pick(payload, "headline", "status")
    what_happened = args.what_happened or _pick(payload, "what_happened", "whatHappened")
    why_it_matters = args.why_it_matters or _pick(payload, "why_it_matters", "whyItMatters")
    next_step = args.next_step or _pick(payload, "next_step", "nextStep")

    evidence = list(args.evidence)
    payload_evidence = payload.get("evidence")
    if isinstance(payload_evidence, list):
        evidence.extend(str(item) for item in payload_evidence)

    memo = build_memo(
        headline=headline,
        what_happened=what_happened,
        why_it_matters=why_it_matters,
        next_step=next_step,
        evidence=evidence,
    )
    print(memo)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
