"""
Self-Healing Element Resolution for Web Benchmarks.

Multi-strategy resolution chain that finds elements using intent descriptions
instead of brittle CSS selectors. Caches successful strategies via LearningStore
for cross-session learning.

Resolution chain:
  1. Exact selector (CSS/XPath hint from task definition)
  2. Playwright semantic locators (get_by_text, get_by_role, get_by_label)
  3. Cached strategy from LearningStore (past successful selectors for this app)
  4. DOM fuzzy match (partial text, placeholder, aria-label search)
  5. LLM vision fallback (screenshot -> gpt-5-mini -> CSS selector)
"""

import json
import logging
from typing import Any, Dict, List, Optional

from playwright.async_api import Page, Locator

logger = logging.getLogger(__name__)


class WebElementResolver:
    """
    Self-healing element resolver with a multi-strategy fallback chain.

    Usage:
        resolver = WebElementResolver()
        locator = await resolver.resolve(
            page,
            intent="the search input",
            selector_hint="input[type='search']",
            app_id="todomvc",
            learning_store=store,
        )
        if locator:
            await locator.click()
    """

    def __init__(self, llm_model: str = "gpt-5-mini"):
        self.llm_model = llm_model
        self.resolution_strategies: List[Dict[str, Any]] = []

    async def resolve(
        self,
        page: Page,
        intent: str,
        selector_hint: str = "",
        app_id: str = "",
        learning_store=None,
    ) -> Optional[Locator]:
        """
        Find an element using a multi-strategy resolution chain.

        Args:
            page: Playwright Page instance
            intent: Human-readable description (e.g. "the search input", "submit button")
            selector_hint: Optional CSS selector hint from task definition
            app_id: App identifier for LearningStore lookups
            learning_store: Optional LearningStore for cached strategy retrieval

        Returns:
            Playwright Locator if found, or None if all strategies fail
        """
        strategies = [
            ("exact_selector", self._try_exact_selector),
            ("playwright_semantic", self._try_semantic),
            ("cached_strategy", self._try_cached),
            ("dom_fuzzy", self._try_dom_fuzzy),
            ("llm_vision", self._try_llm_vision),
        ]

        for strategy_name, strategy_fn in strategies:
            self.resolution_strategies.append({
                "intent": intent,
                "strategy": strategy_name,
                "status": "trying",
                "app_id": app_id,
            })
            try:
                locator, selector_used = await strategy_fn(
                    page, intent, selector_hint, app_id, learning_store
                )
                if locator is not None:
                    logger.info(
                        f"[RESOLVER] Found '{intent}' via {strategy_name} "
                        f"(selector={selector_used})"
                    )
                    self.resolution_strategies[-1].update({
                        "status": "success",
                        "selector": selector_used,
                    })

                    # Cache successful strategy for future runs
                    if learning_store and strategy_name != "cached_strategy":
                        try:
                            learning_store.record_app_pattern(
                                app_id or "unknown",
                                f"element_resolution:{intent}",
                                [f"{strategy_name}|{selector_used}"],
                            )
                        except Exception:
                            pass  # Non-critical

                    return locator
                else:
                    self.resolution_strategies[-1]["status"] = "no_match"
            except Exception as e:
                logger.debug(
                    f"[RESOLVER] Strategy {strategy_name} failed for '{intent}': {e}"
                )
                self.resolution_strategies[-1].update({
                    "status": "error",
                    "error": str(e),
                })
                continue

        logger.warning(f"[RESOLVER] All strategies exhausted for '{intent}'")
        return None

    # ── Strategy 1: Exact CSS selector ────────────────────────────────

    async def _try_exact_selector(
        self, page: Page, intent: str, selector_hint: str, app_id: str, learning_store
    ) -> tuple[Optional[Locator], str]:
        if not selector_hint:
            return None, ""

        locator = page.locator(selector_hint)
        count = await locator.count()
        if count > 0:
            first = locator.first
            if await first.is_visible():
                return first, selector_hint
        return None, ""

    # ── Strategy 2: Playwright semantic locators ──────────────────────

    async def _try_semantic(
        self, page: Page, intent: str, selector_hint: str, app_id: str, learning_store
    ) -> tuple[Optional[Locator], str]:
        """Try Playwright's built-in semantic locators."""
        intent_lower = intent.lower()

        # Determine role from intent keywords
        role_map = {
            "button": "button",
            "link": "link",
            "input": "textbox",
            "text field": "textbox",
            "text input": "textbox",
            "search": "searchbox",
            "search input": "searchbox",
            "checkbox": "checkbox",
            "radio": "radio",
            "select": "combobox",
            "dropdown": "combobox",
            "tab": "tab",
            "heading": "heading",
            "navigation": "navigation",
            "menu": "menu",
            "dialog": "dialog",
        }

        # Try get_by_role with name matching
        for keyword, role in role_map.items():
            if keyword in intent_lower:
                try:
                    # Extract the descriptive part (e.g. "Sign In" from "Sign In button")
                    name_part = intent_lower.replace(keyword, "").strip()
                    name_part = name_part.strip("the ").strip()

                    if name_part:
                        locator = page.get_by_role(role, name=name_part)
                    else:
                        locator = page.get_by_role(role)

                    if await locator.count() > 0 and await locator.first.is_visible():
                        desc = f"get_by_role('{role}', name='{name_part}')"
                        return locator.first, desc
                except Exception:
                    pass

        # Try get_by_text
        try:
            locator = page.get_by_text(intent, exact=False)
            if await locator.count() > 0 and await locator.first.is_visible():
                return locator.first, f"get_by_text('{intent}')"
        except Exception:
            pass

        # Try get_by_label
        try:
            locator = page.get_by_label(intent, exact=False)
            if await locator.count() > 0 and await locator.first.is_visible():
                return locator.first, f"get_by_label('{intent}')"
        except Exception:
            pass

        # Try get_by_placeholder
        try:
            locator = page.get_by_placeholder(intent, exact=False)
            if await locator.count() > 0 and await locator.first.is_visible():
                return locator.first, f"get_by_placeholder('{intent}')"
        except Exception:
            pass

        return None, ""

    # ── Strategy 3: Cached strategy from LearningStore ────────────────

    async def _try_cached(
        self, page: Page, intent: str, selector_hint: str, app_id: str, learning_store
    ) -> tuple[Optional[Locator], str]:
        if not learning_store or not app_id:
            return None, ""

        try:
            patterns = learning_store.get_app_patterns(app_id)
            if not patterns:
                return None, ""

            # Look for cached resolution for this intent
            cache_key = f"element_resolution:{intent}"
            for pattern in patterns:
                if not isinstance(pattern, dict):
                    continue
                goal = pattern.get("goal", "")
                if cache_key in str(goal) or intent in str(goal):
                    # Extract strategy|selector from cached actions
                    cached_actions = pattern.get("actions", [])
                    for cached in cached_actions:
                        if "|" in str(cached):
                            _, selector = str(cached).split("|", 1)
                            try:
                                locator = page.locator(selector)
                                if (
                                    await locator.count() > 0
                                    and await locator.first.is_visible()
                                ):
                                    return locator.first, selector
                            except Exception:
                                pass
        except Exception as e:
            logger.debug(f"Cached strategy lookup failed: {e}")

        return None, ""

    # ── Strategy 4: DOM fuzzy match ───────────────────────────────────

    async def _try_dom_fuzzy(
        self, page: Page, intent: str, selector_hint: str, app_id: str, learning_store
    ) -> tuple[Optional[Locator], str]:
        """Search the DOM for elements matching the intent via JS evaluation."""
        try:
            selector = await page.evaluate(
                """(intent) => {
                const intentLower = intent.toLowerCase();
                const candidates = [];

                // Search by aria-label
                document.querySelectorAll('[aria-label]').forEach(el => {
                    if (el.getAttribute('aria-label').toLowerCase().includes(intentLower)) {
                        candidates.push({el, score: 3, type: 'aria-label'});
                    }
                });

                // Search by placeholder
                document.querySelectorAll('[placeholder]').forEach(el => {
                    if (el.getAttribute('placeholder').toLowerCase().includes(intentLower)) {
                        candidates.push({el, score: 3, type: 'placeholder'});
                    }
                });

                // Search by title attribute
                document.querySelectorAll('[title]').forEach(el => {
                    if (el.getAttribute('title').toLowerCase().includes(intentLower)) {
                        candidates.push({el, score: 2, type: 'title'});
                    }
                });

                // Search by text content (interactive elements only)
                const interactive = 'a, button, input, select, textarea, [role="button"], [role="link"], [role="tab"], [onclick]';
                document.querySelectorAll(interactive).forEach(el => {
                    const text = (el.textContent || '').trim().toLowerCase();
                    if (text && text.includes(intentLower)) {
                        candidates.push({el, score: 2, type: 'textContent'});
                    }
                });

                // Search by name attribute
                document.querySelectorAll('[name]').forEach(el => {
                    if (el.getAttribute('name').toLowerCase().includes(intentLower)) {
                        candidates.push({el, score: 2, type: 'name'});
                    }
                });

                // Search by id
                document.querySelectorAll('[id]').forEach(el => {
                    if (el.id.toLowerCase().includes(intentLower)) {
                        candidates.push({el, score: 1, type: 'id'});
                    }
                });

                // Search by class
                document.querySelectorAll('*').forEach(el => {
                    if (el.className && typeof el.className === 'string' &&
                        el.className.toLowerCase().includes(intentLower)) {
                        candidates.push({el, score: 0.5, type: 'class'});
                    }
                });

                if (candidates.length === 0) return null;

                // Sort by score descending, pick best visible one
                candidates.sort((a, b) => b.score - a.score);
                for (const c of candidates) {
                    const rect = c.el.getBoundingClientRect();
                    if (rect.width > 0 && rect.height > 0) {
                        // Build a unique selector
                        if (c.el.id) return '#' + c.el.id;
                        if (c.type === 'aria-label')
                            return `[aria-label="${c.el.getAttribute('aria-label')}"]`;
                        if (c.type === 'placeholder')
                            return `[placeholder="${c.el.getAttribute('placeholder')}"]`;
                        if (c.type === 'name')
                            return `${c.el.tagName.toLowerCase()}[name="${c.el.getAttribute('name')}"]`;
                        // Fallback: tag + nth-of-type
                        const tag = c.el.tagName.toLowerCase();
                        const siblings = Array.from(c.el.parentElement?.children || [])
                            .filter(s => s.tagName === c.el.tagName);
                        const idx = siblings.indexOf(c.el) + 1;
                        return `${tag}:nth-of-type(${idx})`;
                    }
                }
                return null;
            }""",
                intent,
            )

            if selector:
                locator = page.locator(selector)
                if await locator.count() > 0 and await locator.first.is_visible():
                    return locator.first, selector
        except Exception as e:
            logger.debug(f"DOM fuzzy match failed: {e}")

        return None, ""

    # ── Strategy 5: LLM vision fallback ───────────────────────────────

    async def _try_llm_vision(
        self, page: Page, intent: str, selector_hint: str, app_id: str, learning_store
    ) -> tuple[Optional[Locator], str]:
        """Take a screenshot and ask the LLM to identify the element."""
        try:
            import base64
            from openai import OpenAI

            # Take a screenshot
            screenshot_bytes = await page.screenshot(full_page=False)
            b64_image = base64.b64encode(screenshot_bytes).decode("utf-8")

            # Get DOM structure for context
            dom_snippet = await page.evaluate("""() => {
                const interactiveSelector = 'a, button, input, select, textarea, [role], [onclick], [data-testid]';
                const elements = document.querySelectorAll(interactiveSelector);
                return Array.from(elements).slice(0, 50).map(el => {
                    const rect = el.getBoundingClientRect();
                    return {
                        tag: el.tagName.toLowerCase(),
                        id: el.id || null,
                        name: el.getAttribute('name') || null,
                        type: el.getAttribute('type') || null,
                        text: (el.textContent || '').trim().substring(0, 60),
                        ariaLabel: el.getAttribute('aria-label') || null,
                        placeholder: el.getAttribute('placeholder') || null,
                        role: el.getAttribute('role') || null,
                        visible: rect.width > 0 && rect.height > 0,
                        x: Math.round(rect.x),
                        y: Math.round(rect.y),
                        w: Math.round(rect.width),
                        h: Math.round(rect.height),
                    };
                }).filter(e => e.visible);
            }""")

            client = OpenAI()
            response = client.responses.create(
                model=self.llm_model,
                input=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "input_text",
                                "text": (
                                    f"Find the element on this page that matches: '{intent}'\n\n"
                                    f"Available interactive elements:\n{json.dumps(dom_snippet[:30], indent=2)}\n\n"
                                    "Return ONLY a JSON object with:\n"
                                    '{"selector": "CSS selector", "confidence": 0.0-1.0}\n'
                                    "Use the most specific selector possible (id > aria-label > name > tag)."
                                ),
                            },
                            {
                                "type": "input_image",
                                "image_url": f"data:image/png;base64,{b64_image}",
                            },
                        ],
                    }
                ],
                store=False,
            )

            # Parse response
            text = response.output_text.strip()
            # Extract JSON from potential markdown
            if "```" in text:
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]
                text = text.strip()

            result = json.loads(text)
            css_selector = result.get("selector", "")

            if css_selector:
                locator = page.locator(css_selector)
                if await locator.count() > 0 and await locator.first.is_visible():
                    return locator.first, css_selector
        except ImportError:
            logger.debug("OpenAI not available for LLM vision fallback")
        except Exception as e:
            logger.debug(f"LLM vision fallback failed: {e}")

        return None, ""
