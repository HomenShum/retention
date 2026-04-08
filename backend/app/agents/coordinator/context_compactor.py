"""
Context Compactor - Smart semantic compaction for agent context management.

This module provides intelligent context compaction that preserves semantic meaning
while reducing token usage. Based on LangChain/LangMem patterns:
- Summarize large outputs instead of truncating
- Store full data externally with retrieval capability
- Group and categorize data for efficient representation

Key strategies:
1. Element List Compaction: Group by type, show actionable elements
2. Tool Output Compaction: Route to appropriate compactor by tool name
3. Conversation Summarization: Compress old messages while keeping recent ones
"""

import logging
import hashlib
import json
from typing import Dict, Any, List, Optional
from collections import defaultdict
from datetime import datetime

logger = logging.getLogger(__name__)

# External storage for full outputs (in-memory for now, can be Redis later)
_output_storage: Dict[str, Dict[str, Any]] = {}


def store_full_output(data: Any, tool_name: str) -> str:
    """Store full output and return a reference ID for retrieval."""
    content = json.dumps(data) if not isinstance(data, str) else data
    ref_id = hashlib.md5(f"{tool_name}:{datetime.now().isoformat()}:{content[:100]}".encode()).hexdigest()[:12]
    _output_storage[ref_id] = {
        "tool_name": tool_name,
        "content": content,
        "stored_at": datetime.now().isoformat(),
        "size_chars": len(content)
    }
    logger.info(f"[CONTEXT] Stored full output for {tool_name} with ref_id={ref_id} ({len(content)} chars)")
    return ref_id


def get_full_output(ref_id: str) -> Optional[str]:
    """Retrieve full output by reference ID."""
    if ref_id in _output_storage:
        logger.info(f"[CONTEXT] Retrieved full output for ref_id={ref_id}")
        return _output_storage[ref_id]["content"]
    logger.warning(f"[CONTEXT] ref_id={ref_id} not found in storage")
    return None


def get_storage_info(ref_id: str) -> Optional[Dict[str, Any]]:
    """Get metadata about stored output."""
    if ref_id in _output_storage:
        stored = _output_storage[ref_id]
        return {
            "ref_id": ref_id,
            "tool_name": stored["tool_name"],
            "stored_at": stored["stored_at"],
            "size_chars": stored["size_chars"]
        }
    return None


def list_stored_outputs() -> List[Dict[str, Any]]:
    """List all stored outputs with metadata (for debugging)."""
    return [
        {
            "ref_id": ref_id,
            "tool_name": data["tool_name"],
            "stored_at": data["stored_at"],
            "size_chars": data["size_chars"]
        }
        for ref_id, data in _output_storage.items()
    ]


def clear_old_outputs(max_age_seconds: int = 3600) -> int:
    """Clear outputs older than max_age_seconds. Returns count of cleared items."""
    from datetime import datetime, timedelta
    cutoff = datetime.now() - timedelta(seconds=max_age_seconds)
    cleared = 0
    keys_to_delete = []
    for ref_id, data in _output_storage.items():
        stored_at = datetime.fromisoformat(data["stored_at"])
        if stored_at < cutoff:
            keys_to_delete.append(ref_id)
            cleared += 1
    for key in keys_to_delete:
        del _output_storage[key]
    if cleared:
        logger.info(f"[CONTEXT] Cleared {cleared} old outputs from storage")
    return cleared


def compact_element_list(elements: List[Dict], max_actionable: int = 15) -> str:
    """
    Compact a list of UI elements into a semantic summary.
    
    Strategy:
    - Group elements by type with counts
    - Show ALL actionable elements (clickable/editable) with coordinates
    - Provide structure without overwhelming detail
    
    Args:
        elements: List of element dictionaries from list_elements_on_screen
        max_actionable: Max actionable elements to show in detail (default 15)
    
    Returns:
        Compact string representation preserving navigation capability
    """
    if not elements:
        return "No elements found on screen."
    
    if len(elements) <= max_actionable:
        # Small list - return as-is in compact format
        return _format_elements_compact(elements)
    
    # Group by element type
    by_type = defaultdict(list)
    for el in elements:
        el_type = el.get("type", el.get("class", "unknown"))
        by_type[el_type].append(el)
    
    # Build compact summary
    lines = [f"📱 Screen Elements: {len(elements)} total"]
    lines.append("")
    
    # Type breakdown
    lines.append("## Element Types:")
    for el_type, type_els in sorted(by_type.items(), key=lambda x: -len(x[1])):
        examples = [el.get("text", el.get("content-desc", ""))[:25] for el in type_els[:3] if el.get("text") or el.get("content-desc")]
        examples_str = f' (e.g., {", ".join(examples)})' if examples else ""
        lines.append(f"  - {el_type}: {len(type_els)}{examples_str}")
    
    # Actionable elements (clickable, editable, focusable)
    actionable = [el for el in elements if el.get("clickable") or el.get("focusable") or el.get("editable") or "Button" in str(el.get("type", "")) or "EditText" in str(el.get("type", ""))]
    
    lines.append("")
    lines.append(f"## Actionable Elements ({len(actionable)}):")
    
    for i, el in enumerate(actionable[:max_actionable]):
        el_type = el.get("type", el.get("class", "?"))
        text = el.get("text", el.get("content-desc", ""))[:40] or "[no text]"
        bounds = el.get("bounds", {})
        x = bounds.get("x", el.get("x", "?"))
        y = bounds.get("y", el.get("y", "?"))
        lines.append(f"  {i+1}. [{el_type}] \"{text}\" @ ({x}, {y})")
    
    if len(actionable) > max_actionable:
        lines.append(f"  ... and {len(actionable) - max_actionable} more actionable elements")
    
    return "\n".join(lines)


def _format_elements_compact(elements: List[Dict]) -> str:
    """Format small element list in compact but complete format."""
    lines = [f"📱 Screen Elements: {len(elements)} total", ""]
    for i, el in enumerate(elements):
        el_type = el.get("type", el.get("class", "?"))
        text = el.get("text", el.get("content-desc", ""))[:50] or "[no text]"
        bounds = el.get("bounds", {})
        x = bounds.get("x", el.get("x", "?"))
        y = bounds.get("y", el.get("y", "?"))
        clickable = "✓" if el.get("clickable") else ""
        lines.append(f"  {i+1}. [{el_type}] \"{text}\" @ ({x}, {y}) {clickable}")
    return "\n".join(lines)


def compact_tool_output(output: str, tool_name: str, max_chars: int = 4000) -> str:
    """
    Smart compaction of tool output based on tool type.
    
    Routes to appropriate compactor:
    - list_elements_on_screen -> compact_element_list
    - take_screenshot -> pass through (already returns text analysis)
    - Other tools -> generic compaction with truncation fallback
    """
    if len(output) <= max_chars:
        return output
    
    logger.info(f"[CONTEXT] Compacting {tool_name} output: {len(output)} chars -> max {max_chars}")
    
    # Route to specific compactors
    if "list_elements" in tool_name.lower() or "elements_on_screen" in tool_name.lower():
        try:
            data = json.loads(output)
            elements = data.get("elements", [])
            if elements:
                ref_id = store_full_output(elements, tool_name)
                compact = compact_element_list(elements)
                return f"{compact}\n\n[Full list stored: ref_id={ref_id}]"
        except json.JSONDecodeError:
            pass  # Fall through to generic
    
    # Check for base64 data
    if len(output) > 10000 and ' ' not in output[:1000] and '\n' not in output[:1000]:
        logger.warning(f"[CONTEXT] Detected base64 data in {tool_name}, storing externally")
        ref_id = store_full_output(output, tool_name)
        return f"[Large binary data stored externally: ref_id={ref_id}, size={len(output)} chars]"
    
    # Generic truncation with context
    ref_id = store_full_output(output, tool_name)
    truncated = output[:max_chars]
    return f"{truncated}\n\n[... truncated. Full output stored: ref_id={ref_id}]"

