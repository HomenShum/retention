"""
BFS Crawl Tools — screen registry, fingerprinting, and queue management.

`create_crawl_tools(device_id)` returns:
  - A dict of tool functions (for the crawl agent)
  - A `get_result()` closure that returns the final CrawlResult
"""

import hashlib
import json
import logging
from collections import deque
from typing import Any, Callable, Dict, List, Optional, Tuple

from ..schemas import (
    ComponentInfo,
    CrawlResult,
    ScreenNode,
    ScreenTransition,
)

logger = logging.getLogger(__name__)

# ── Crawl budget constants ────────────────────────────────────────────────
MAX_ELEMENTS_PER_SCREEN = 3   # Demo mode: only top-3 nav-priority elements per screen
MAX_DEPTH = 2                  # Demo mode: max 2 levels deep (home → screen → done)
TURNS_PER_ELEMENT = 6          # Tool calls per cycle: get_next_target + tap_by_text + get_ui_elements + list_elements + register + optional wait
STARTUP_SHUTDOWN_TURNS = 6     # Budget reserved for launch_app + first screenshot + list + register + complete_crawl

# ── Chrome UI filter (for web app crawling) ───────────────────────────────
# Resource IDs and text patterns for Chrome's own shell UI.
# These should NOT be enqueued as BFS targets when crawling a web app.
_CHROME_RESOURCE_ID_PREFIXES = (
    "com.android.chrome:id/",
    "com.chrome.beta:id/",
    "com.google.android.apps.chrome:id/",
    "org.chromium.chrome:id/",
)
_CHROME_UI_TEXTS = frozenset({
    "search or type url",
    "search or type web address",
    "new tab",
    "close tab",
    "tabs",
    "more options",
    "close all tabs",
    "new incognito tab",
})


def _is_chrome_ui_element(raw_component: dict) -> bool:
    """Return True if this element belongs to Chrome's shell UI, not the web page."""
    # Check resource_id (available in ADB fallback data)
    rid = raw_component.get("resource_id", "") or raw_component.get("resource-id", "") or ""
    if rid and any(rid.startswith(prefix) for prefix in _CHROME_RESOURCE_ID_PREFIXES):
        return True

    # Check class name for Chrome-specific widgets
    cls = (raw_component.get("class", "") or raw_component.get("element_type", "") or "").lower()
    if "urlbar" in cls or "omnibox" in cls:
        return True

    # Check text patterns
    text = (raw_component.get("text", "") or "").strip().lower()
    if text in _CHROME_UI_TEXTS:
        return True

    return False


def _element_nav_priority(component: ComponentInfo) -> int:
    """Lower number = higher priority. Nav elements explored first."""
    etype = (component.element_type or "").lower()
    text = (component.text or "").lower()

    # Navigation elements (tabs, menus, sidebar items) — explore first
    if any(k in etype for k in ("nav", "tab", "menu", "drawer", "sidebar", "toolbar")):
        return 0
    if any(k in text for k in ("menu", "settings", "home", "profile")):
        return 0

    # Buttons / FABs — likely lead to new screens
    if any(k in etype for k in ("button", "fab", "imagebutton")):
        return 1

    # Links and clickable text
    if "link" in etype or "textview" in etype:
        return 2

    # Everything else
    return 3


def create_crawl_tools(
    device_id: str,
    max_turns: int = 40,
    is_web_crawl: bool = False,
    app_name: str = "",
    app_url: str = "",
    package_name: str = "",
) -> Tuple[Dict[str, Callable], Callable, Callable]:
    """
    Create BFS crawl infrastructure tools.

    Args:
        device_id: Target device identifier
        max_turns: Maximum agent turns available (used to compute exploration budget)
        is_web_crawl: If True, filter out Chrome browser UI elements from the BFS queue
        app_name: App name for path memory persistence
        app_url: App URL for path memory key
        package_name: Android package for path memory key

    Returns:
        (tool_dict, get_result, get_fingerprints) where tool_dict maps tool names
        to async functions, get_result() returns the CrawlResult, and
        get_fingerprints() returns the fingerprint→screen_id map.
    """

    # ── Exploration Memory: load prior screen fingerprints ───────────────────
    from ..exploration_memory import app_fingerprint, get_screen_fingerprints
    _prior_screen_fps: Dict[str, str] = {}  # screen_id -> screen_fingerprint
    _prior_screen_count = 0
    _app_key = ""

    logger.info(f"create_crawl_tools called: app_name='{app_name}', app_url='{app_url}', package='{package_name}'")

    if app_name or app_url or package_name:
        _app_key = app_fingerprint(app_url, package_name, app_name)
        _prior_screen_fps = get_screen_fingerprints(_app_key)
        _prior_screen_count = len(_prior_screen_fps)
        if _prior_screen_fps:
            logger.info(
                f"Exploration memory loaded: {_prior_screen_count} prior screen fingerprints "
                f"for app_key={_app_key}"
            )

    # ── Closure state ────────────────────────────────────────────────────────
    screens: Dict[str, ScreenNode] = {}           # screen_id -> ScreenNode
    transitions: List[ScreenTransition] = []
    fingerprints: Dict[str, str] = {}             # fingerprint_hash -> screen_id
    bfs_queue: deque = deque()                    # (screen_id, element_id, coords, text)
    explored_elements: set = set()                # (screen_id, element_id) already popped
    click_history: List[Dict[str, Any]] = []      # {screen_id, element_id, text, outcome}
    screen_counter = 0
    total_components = 0
    explorations_done = 0  # Track how many BFS elements we've popped
    exploration_budget = max(4, (max_turns - STARTUP_SHUTDOWN_TURNS) // TURNS_PER_ELEMENT)
    trajectory_plan: List[Dict] = []              # Saved by save_trajectory_plan
    trajectory_index = 0                          # Current position in plan
    logger.info(f"Crawl budget: {exploration_budget} explorations from {max_turns} max_turns")

    def _fingerprint(components_json: str) -> str:
        """Hash of sorted (element_type, text) tuples to detect revisits."""
        try:
            components = json.loads(components_json) if isinstance(components_json, str) else components_json
            sig = sorted((c.get("element_type", ""), c.get("text", "")) for c in components)
            return hashlib.md5(json.dumps(sig).encode()).hexdigest()
        except Exception:
            return ""

    # ── Tool: register_screen ────────────────────────────────────────────────

    def register_screen(
        screen_name: str,
        screenshot_description: str,
        screenshot_path: str,
        components_json: str,
        parent_screen_id: str = "",
        trigger_action: str = "",
    ) -> str:
        """
        Register a discovered screen in the crawl registry.

        Fingerprints the component list to detect duplicate screens reached via
        different paths. Enqueues all interactive elements for BFS exploration.

        Args:
            screen_name: Human-readable name (e.g. "Contact List")
            screenshot_description: Vision analysis text describing the screen
            screenshot_path: File path to the saved screenshot
            components_json: JSON string of component list. Each component:
                {"element_id": int, "element_type": str, "text": str,
                 "coordinates": {"x": int, "y": int, "width": int, "height": int},
                 "is_interactive": bool}
            parent_screen_id: ID of the screen navigated from (empty for home)
            trigger_action: Action that led to this screen (e.g. "Tapped FAB")

        Returns:
            Status string with the assigned screen_id or duplicate notice
        """
        nonlocal screen_counter, total_components

        try:
            components_list = json.loads(components_json) if isinstance(components_json, str) else components_json
        except json.JSONDecodeError:
            return "Error: components_json is not valid JSON"

        normalized_parent_screen_id = (parent_screen_id or "").strip()
        if normalized_parent_screen_id and normalized_parent_screen_id not in screens:
            logger.warning(
                f"Ignoring unknown parent_screen_id={normalized_parent_screen_id} while registering '{screen_name}'"
            )
            normalized_parent_screen_id = ""

        # Check for duplicate via fingerprint
        fp = _fingerprint(components_list)
        if fp and fp in fingerprints:
            existing_id = fingerprints[fp]
            logger.info(f"Duplicate screen detected (fingerprint matches {existing_id})")
            # Record this as a duplicate outcome in click history
            if normalized_parent_screen_id and trigger_action:
                click_history.append({
                    "from_screen": normalized_parent_screen_id,
                    "action": trigger_action,
                    "outcome": f"DUPLICATE (same as {existing_id})",
                })
            visited = ", ".join(f"{s.screen_id} '{s.screen_name}'" for s in screens.values())
            return (
                f"DUPLICATE — this screen matches {existing_id} ({screens[existing_id].screen_name}). Skipping.\n"
                f"Already visited: {visited}\n"
                f"→ Call get_next_target for your next action."
            )

        # Register new screen
        screen_counter += 1
        screen_id = f"screen_{screen_counter:03d}"

        # Calculate navigation depth
        depth = 0
        if normalized_parent_screen_id:
            depth = screens[normalized_parent_screen_id].navigation_depth + 1

        chrome_filtered = 0
        components = []
        for c in components_list:
            # For web crawls, skip Chrome shell UI elements before they enter the queue
            if is_web_crawl and _is_chrome_ui_element(c):
                chrome_filtered += 1
                continue

            comp = ComponentInfo(
                element_id=c.get("element_id", 0),
                element_type=c.get("element_type", "ELEM"),
                text=c.get("text", ""),
                coordinates=c.get("coordinates", {"x": 0, "y": 0, "width": 0, "height": 0}),
                is_interactive=c.get("is_interactive", False),
            )
            components.append(comp)

        if chrome_filtered > 0:
            logger.info(f"Filtered {chrome_filtered} Chrome UI elements from screen")

        # ── Exploration Memory: check if screen matches a prior crawl ──
        # Compare screen name against prior screen graph. If the screen name
        # exists in prior data, it's likely unchanged — reduce BFS depth.
        is_known_from_memory = False
        if _prior_screen_fps and screen_name:
            # Check if any prior screen has the same name
            prior_names = {sid: fp_val for sid, fp_val in _prior_screen_fps.items()}
            for prior_sid, prior_fp_val in prior_names.items():
                # Simple heuristic: if screen name matches a prior screen's ID pattern,
                # the screen structure is likely unchanged
                if screen_name.lower().strip() in str(prior_fp_val).lower():
                    is_known_from_memory = True
                    logger.info(
                        f"Exploration memory match: '{screen_name}' matches prior {prior_sid}. "
                        f"Reducing BFS depth for known screen."
                    )
                    break

            # Better approach: compute screen fingerprint using same method as
            # exploration_memory and compare directly
            if not is_known_from_memory:
                from ..exploration_memory import screen_fingerprint as em_screen_fp
                current_node_for_fp = ScreenNode(
                    screen_id=f"temp_{screen_counter}",
                    screen_name=screen_name,
                    screenshot_path=screenshot_path,
                    screenshot_description=screenshot_description,
                    navigation_depth=0,
                    components=components,
                )
                current_fp = em_screen_fp(current_node_for_fp)
                if current_fp in _prior_screen_fps.values():
                    is_known_from_memory = True
                    logger.info(
                        f"Exploration memory fingerprint match: '{screen_name}' "
                        f"(fp={current_fp[:8]}...) found in prior crawl."
                    )

        # Sort interactive elements by nav priority before enqueueing
        # Cap per screen and respect depth limit to stay within turn budget
        interactive_components = [comp for comp in components if comp.is_interactive]
        interactive_components.sort(key=_element_nav_priority)

        # Exploration memory optimization: known-unchanged screens get fewer BFS slots
        max_elements_this_screen = MAX_ELEMENTS_PER_SCREEN
        if is_known_from_memory:
            max_elements_this_screen = max(2, MAX_ELEMENTS_PER_SCREEN // 3)
            logger.info(f"Exploration memory: limiting BFS to {max_elements_this_screen} elements (known screen)")

        enqueued = 0
        if depth <= MAX_DEPTH:
            for comp in interactive_components:
                if enqueued >= max_elements_this_screen:
                    break
                key = (screen_id, comp.element_id)
                if key not in explored_elements:
                    bfs_queue.append((
                        screen_id,
                        comp.element_id,
                        comp.coordinates,
                        comp.text,
                    ))
                    enqueued += 1
        else:
            logger.info(f"Skipping enqueue for {screen_id} at depth {depth} (max {MAX_DEPTH})")

        skipped = len(interactive_components) - enqueued

        total_components += len(components)

        node = ScreenNode(
            screen_id=screen_id,
            screen_name=screen_name,
            screenshot_path=screenshot_path,
            screenshot_description=screenshot_description,
            navigation_depth=depth,
            parent_screen_id=normalized_parent_screen_id or None,
            trigger_action=trigger_action or None,
            components=components,
        )
        screens[screen_id] = node
        if fp:
            fingerprints[fp] = screen_id

        interactive_count = sum(1 for c in components if c.is_interactive)
        logger.info(
            f"Registered {screen_id}: '{screen_name}' — "
            f"{len(components)} components ({interactive_count} interactive), depth={depth}"
        )

        # ── Incremental exploration memory save (crash-resilient) ──
        # Save after every 2+ screens so memory survives backend OOM/restarts
        if _app_key and len(screens) >= 2:
            try:
                from ..exploration_memory import store_crawl
                store_crawl(_app_key, _build_result(), app_url=app_url, app_name=app_name)
            except Exception as e:
                logger.debug(f"Incremental exploration memory save failed: {e}")

        # Record click that led here
        if normalized_parent_screen_id and trigger_action:
            click_history.append({
                "from_screen": normalized_parent_screen_id,
                "action": trigger_action,
                "outcome": f"→ {screen_id} '{screen_name}'",
            })

        visited = ", ".join(f"{s.screen_id} '{s.screen_name}'" for s in screens.values())
        budget_remaining = exploration_budget - explorations_done
        budget_info = f"Budget: {explorations_done}/{exploration_budget} explorations used"
        if skipped > 0:
            budget_info += f" ({skipped} low-priority elements skipped)"
        return (
            f"Registered {screen_id}: '{screen_name}' — "
            f"{len(components)} components ({interactive_count} interactive, {enqueued} enqueued), "
            f"depth={depth}, BFS queue={len(bfs_queue)}\n"
            f"{budget_info}\n"
            f"Already visited: {visited}\n"
            f"→ Call get_next_target for your next action."
        )

    # ── Tool: mark_transition ────────────────────────────────────────────────

    def mark_transition(
        from_screen_id: str,
        to_screen_id: str,
        action_description: str,
        component_id: int = -1,
    ) -> str:
        """
        Record a navigation edge between two screens.

        Args:
            from_screen_id: Source screen (e.g. "screen_001")
            to_screen_id: Destination screen (e.g. "screen_002")
            action_description: Human-readable action (e.g. "Tap 'Create contact' FAB")
            component_id: ID of the component that triggered the transition (-1 if unknown)

        Returns:
            Confirmation message
        """
        trans = ScreenTransition(
            from_screen=from_screen_id,
            to_screen=to_screen_id,
            action=action_description,
            component_id=component_id if component_id >= 0 else None,
        )
        transitions.append(trans)

        # Update leads_to on the source component
        if from_screen_id in screens and component_id >= 0:
            for comp in screens[from_screen_id].components:
                if comp.element_id == component_id:
                    comp.leads_to = to_screen_id
                    break

        return f"Transition recorded: {from_screen_id} -> {to_screen_id} via '{action_description}'"

    # ── Tool: get_crawl_status ───────────────────────────────────────────────

    def get_crawl_status() -> str:
        """
        Get current crawl progress statistics.

        Returns:
            JSON string with screens_found, components_found, bfs_queue_depth,
            screens list, and next targets preview.
        """
        next_targets = []
        for item in list(bfs_queue)[:3]:
            sid, eid, coords, text = item
            sname = screens[sid].screen_name if sid in screens else sid
            next_targets.append(f"Screen '{sname}', element #{eid} '{text}' at ({coords.get('x', 0)}, {coords.get('y', 0)})")

        status = {
            "screens_found": len(screens),
            "total_components": total_components,
            "bfs_queue_depth": len(bfs_queue),
            "screens": [{"id": s.screen_id, "name": s.screen_name, "depth": s.navigation_depth} for s in screens.values()],
            "next_targets": next_targets,
        }
        return json.dumps(status, indent=2)

    # ── Tool: get_next_target ────────────────────────────────────────────────

    def get_next_target() -> str:
        """
        Pop the next unexplored element from the BFS queue.

        Returns:
            Instructions for the next element to explore, or completion signal.
        """
        nonlocal explorations_done

        # Budget check — auto-signal completion when budget is exhausted
        if explorations_done >= exploration_budget:
            logger.info(f"Exploration budget exhausted ({explorations_done}/{exploration_budget})")
            return (
                f"BUDGET_EXHAUSTED — explored {explorations_done} elements (budget: {exploration_budget}). "
                f"Call complete_crawl now to finalize results."
            )

        while bfs_queue:
            sid, eid, coords, text = bfs_queue.popleft()
            key = (sid, eid)
            if key in explored_elements:
                continue
            explored_elements.add(key)

            screen = screens.get(sid)
            if not screen:
                continue

            explorations_done += 1
            cx = coords.get("x", 0) + coords.get("width", 0) // 2
            cy = coords.get("y", 0) + coords.get("height", 0) // 2
            remaining = exploration_budget - explorations_done

            # Prefer tap_by_text for precise interaction; fall back to coords only if text is empty
            if text and text.strip():
                tap_instruction = f"Use tap_by_text('{text}') to tap this element."
            else:
                tap_instruction = f"No text label — use click_at_coordinates({cx}, {cy}) as fallback."

            return (
                f"NEXT TARGET: element #{eid} '{text}' on screen '{screen.screen_name}' ({sid}). "
                f"{tap_instruction} "
                f"After tapping: get_ui_elements → list_elements_on_screen → register_screen (if new screen). "
                f"[Budget: {remaining} explorations left, queue: {len(bfs_queue)}]"
            )

        return "QUEUE_EMPTY — BFS exploration complete. Call complete_crawl to finalize."

    # ── Tool: get_exploration_log ────────────────────────────────────────────

    def get_exploration_log() -> str:
        """
        Get a full exploration log: visited screens, click history, and BFS queue state.

        Call this when you need to understand what has already been explored
        before deciding your next action.

        Returns:
            Markdown-formatted exploration log with screens, clicks, and queue info.
        """
        lines = ["=== EXPLORATION LOG ==="]

        # Screens visited
        lines.append(f"\nScreens visited ({len(screens)}):")
        for s in screens.values():
            interactive = sum(1 for c in s.components if c.is_interactive)
            lines.append(f"  - {s.screen_id}: \"{s.screen_name}\" (depth={s.navigation_depth}) — {interactive} interactive elements")

        # Click history
        total_interactive = sum(
            sum(1 for c in s.components if c.is_interactive)
            for s in screens.values()
        )
        lines.append(f"\nClick history ({len(click_history)} actions, {len(explored_elements)} of {total_interactive} elements explored):")
        for ch in click_history[-15:]:  # Last 15 to avoid token bloat
            lines.append(f"  - {ch['from_screen']}: {ch['action']} {ch['outcome']}")
        if len(click_history) > 15:
            lines.append(f"  ... ({len(click_history) - 15} earlier actions omitted)")

        # BFS queue
        lines.append(f"\nBFS queue remaining: {len(bfs_queue)} elements")
        for item in list(bfs_queue)[:3]:
            sid, eid, coords, text = item
            sname = screens[sid].screen_name if sid in screens else sid
            x = coords.get("x", 0) + coords.get("width", 0) // 2
            y = coords.get("y", 0) + coords.get("height", 0) // 2
            lines.append(f"  Next: {sname} ({sid}) element #{eid} \"{text}\" at ({x}, {y})")

        return "\n".join(lines)

    # ── Tool: save_trajectory_plan ───────────────────────────────────────────

    def save_trajectory_plan(trajectories_json: str) -> str:
        """
        Store your prioritized crawl plan after analyzing the home screen.
        Call once during PHASE 1, before starting PHASE 2.

        Args:
            trajectories_json: JSON array of trajectory objects.
                Each object: {"name": str, "entry_element": str, "goal": str, "priority": int}

        Returns:
            Confirmation with plan summary.
        """
        nonlocal trajectory_plan, trajectory_index
        try:
            plans = json.loads(trajectories_json) if isinstance(trajectories_json, str) else trajectories_json
            if not isinstance(plans, list):
                return "Error: trajectories_json must be a JSON array."
            plans.sort(key=lambda x: x.get("priority", 99))
            trajectory_plan = plans
            trajectory_index = 0
            summary = " | ".join(
                f"[{i+1}] {p.get('name', '?')} → {p.get('entry_element', '?')}"
                for i, p in enumerate(plans)
            )
            logger.info(f"Trajectory plan saved: {len(plans)} trajectories")
            return (
                f"Plan saved — {len(plans)} trajectories:\n{summary}\n"
                f"→ Begin PHASE 2: call get_next_trajectory."
            )
        except Exception as e:
            return f"Error saving plan: {e}"

    # ── Tool: get_next_trajectory ────────────────────────────────────────────

    def get_next_trajectory() -> str:
        """
        Get the next trajectory from your saved plan.
        Call this at the start of each PHASE 2 iteration.

        Returns:
            Next trajectory details, or PLAN_COMPLETE / BUDGET_EXHAUSTED / NO_PLAN signal.
        """
        nonlocal trajectory_index, explorations_done

        if not trajectory_plan:
            return (
                "NO_PLAN — You have not called save_trajectory_plan yet. "
                "Do NOT use get_next_target. "
                "Return to PHASE 1: call get_ui_elements, analyze the home screen, "
                "build your trajectory list, and call save_trajectory_plan first."
            )

        if explorations_done >= exploration_budget:
            return (
                f"BUDGET_EXHAUSTED — {explorations_done}/{exploration_budget} explorations used. "
                f"Call complete_crawl."
            )

        if trajectory_index >= len(trajectory_plan):
            return (
                f"PLAN_COMPLETE — all {len(trajectory_plan)} trajectories explored. "
                f"Call complete_crawl."
            )

        t = trajectory_plan[trajectory_index]
        trajectory_index += 1
        explorations_done += 1  # Count each trajectory as one exploration unit
        remaining = len(trajectory_plan) - trajectory_index
        budget_left = exploration_budget - explorations_done

        return (
            f"TRAJECTORY {trajectory_index}/{len(trajectory_plan)}: '{t.get('name', '?')}'\n"
            f"Goal:  {t.get('goal', '?')}\n"
            f"Entry: tap_by_text('{t.get('entry_element', '?')}')\n"
            f"[{remaining} trajectories remaining | budget: {budget_left} explorations left]"
        )

    # ── Tool: complete_crawl ─────────────────────────────────────────────────

    def complete_crawl() -> str:
        """
        Finalize the crawl and return the CrawlResult as JSON.

        Call this when the BFS queue is empty or you've used enough actions.

        Returns:
            JSON string of the complete CrawlResult
        """
        result = _build_result()

        # ── Exploration Memory: final save ──────────────────────────────
        if _app_key and result.total_screens > 0:
            try:
                from ..exploration_memory import store_crawl
                store_crawl(_app_key, result, app_url=app_url, app_name=app_name)
                logger.info(f"Exploration memory updated for app_key={_app_key}")
            except Exception as e:
                logger.warning(f"Failed to save exploration memory: {e}")

        # ── Persist click history (action pathing) ────────────────────
        if click_history:
            try:
                import json as _json
                from pathlib import Path as _Path
                from datetime import datetime as _dt, timezone as _tz
                pathing_dir = _Path(__file__).resolve().parents[4] / "data" / "action_pathing"
                pathing_dir.mkdir(parents=True, exist_ok=True)
                pathing_file = pathing_dir / f"{_app_key or 'unknown'}_{_dt.now(_tz.utc).strftime('%Y%m%d_%H%M%S')}.json"
                pathing_data = {
                    "app_name": app_name,
                    "app_url": app_url,
                    "recorded_at": _dt.now(_tz.utc).isoformat(),
                    "total_actions": len(click_history),
                    "screens_visited": len(screens),
                    "actions": click_history,
                }
                with open(pathing_file, "w") as pf:
                    _json.dump(pathing_data, pf, indent=2, default=str)
                logger.info(f"Action pathing saved: {len(click_history)} actions → {pathing_file.name}")
            except Exception as e:
                logger.warning(f"Failed to save action pathing: {e}")

        logger.info(
            f"Crawl complete: {result.total_screens} screens, "
            f"{result.total_components} components, {len(result.transitions)} transitions"
        )
        return result.model_dump_json(indent=2)

    # ── Internal: build result ───────────────────────────────────────────────

    def _build_result() -> CrawlResult:
        return CrawlResult(
            app_name="",  # Will be filled by orchestrator
            package_name="",
            screens=list(screens.values()),
            transitions=transitions,
            total_components=total_components,
            total_screens=len(screens),
        )

    # ── Public accessor ──────────────────────────────────────────────────────

    def get_result() -> CrawlResult:
        """Return the current CrawlResult (for use by the orchestrator)."""
        return _build_result()

    def get_fingerprints() -> Dict[str, str]:
        """Return the fingerprint hash → screen_id mapping (for path memory persistence)."""
        return dict(fingerprints)

    # ── Return ───────────────────────────────────────────────────────────────

    tool_dict = {
        "register_screen": register_screen,
        "mark_transition": mark_transition,
        "get_crawl_status": get_crawl_status,
        "save_trajectory_plan": save_trajectory_plan,
        "get_next_trajectory": get_next_trajectory,
        "get_next_target": get_next_target,
        "get_exploration_log": get_exploration_log,
        "complete_crawl": complete_crawl,
    }

    return tool_dict, get_result, get_fingerprints
