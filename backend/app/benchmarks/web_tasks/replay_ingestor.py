"""
Phase 4 - Session Replay Ingestion for Web Benchmarks.

Converts session recordings from PostHog, HAR files, and raw rrweb events
into BenchmarkTask objects that can be registered and run.
"""

import hashlib
import logging
from typing import Dict, List, Optional, Set, Tuple
from urllib.parse import urlparse

from .task_registry import BenchmarkTask, TaskBucket

logger = logging.getLogger(__name__)


class ReplayIngestor:
    """Ingest session replay data from multiple formats into BenchmarkTasks."""

    # ------------------------------------------------------------------ #
    #  Public ingestors
    # ------------------------------------------------------------------ #

    def ingest_posthog(self, replay_json: dict) -> List[BenchmarkTask]:
        """Convert PostHog session recording events to benchmark tasks.

        PostHog events have $autocapture with $event_type (click, change, submit),
        $elements list with tag_name, text, attributes, etc.
        Extract page URLs, click targets, form submissions, errors.
        """
        tasks: List[BenchmarkTask] = []
        seen: Set[str] = set()
        index = 0

        events = self._safe_list(replay_json, "events", fallback_key="results")

        for event in events:
            try:
                event_name = event.get("event", "")
                properties = event.get("properties", {})
                current_url = properties.get("$current_url", "")
                event_type = properties.get("$event_type", "")
                elements = properties.get("$elements", [])

                if event_name == "$autocapture":
                    task = self._posthog_autocapture_to_task(
                        event_type, elements, current_url, index, seen,
                    )
                    if task:
                        tasks.append(task)
                        index += 1

                elif event_name == "$pageview":
                    task = self._posthog_pageview_to_task(
                        current_url, index, seen,
                    )
                    if task:
                        tasks.append(task)
                        index += 1

                elif event_name == "$exception" or event_name == "$error":
                    task = self._posthog_error_to_task(
                        properties, current_url, index, seen,
                    )
                    if task:
                        tasks.append(task)
                        index += 1

            except Exception as exc:
                logger.warning("Skipping PostHog event due to error: %s", exc)

        logger.info("Ingested %d tasks from PostHog replay (%d events)", len(tasks), len(events))
        return tasks

    def ingest_har(self, har_data: dict) -> List[BenchmarkTask]:
        """Convert HAR (HTTP Archive) data to benchmark tasks.

        HAR has log.entries[] with request.url, request.method, response.status.
        Generate load tasks for page navigations, form tasks for POST requests,
        error tasks for 4xx/5xx responses.
        """
        tasks: List[BenchmarkTask] = []
        seen: Set[str] = set()
        index = 0

        log = har_data.get("log", har_data)
        entries = self._safe_list(log, "entries")

        for entry in entries:
            try:
                request = entry.get("request", {})
                response = entry.get("response", {})
                url = request.get("url", "")
                method = request.get("method", "GET").upper()
                status = response.get("status", 0)
                mime_type = (
                    response.get("content", {}).get("mimeType", "")
                )

                # Error responses (4xx / 5xx)
                if status >= 400:
                    task = self._har_error_to_task(
                        url, method, status, index, seen,
                    )
                    if task:
                        tasks.append(task)
                        index += 1
                    continue

                # POST / PUT / PATCH -> form submission tasks
                if method in ("POST", "PUT", "PATCH"):
                    task = self._har_form_to_task(
                        url, method, request, index, seen,
                    )
                    if task:
                        tasks.append(task)
                        index += 1
                    continue

                # GET for HTML documents -> navigation / load tasks
                if method == "GET" and "html" in mime_type:
                    task = self._har_navigation_to_task(
                        url, index, seen,
                    )
                    if task:
                        tasks.append(task)
                        index += 1

            except Exception as exc:
                logger.warning("Skipping HAR entry due to error: %s", exc)

        logger.info("Ingested %d tasks from HAR data (%d entries)", len(tasks), len(entries))
        return tasks

    def ingest_rrweb(self, events: list) -> List[BenchmarkTask]:
        """Convert raw rrweb events to benchmark tasks.

        rrweb event types:
            DomContentLoaded=0, Load=1, FullSnapshot=2,
            IncrementalSnapshot=3, Meta=4, Custom=5, Plugin=6.
        IncrementalSnapshot data.source values:
            Mutation=0, MouseMove=1, MouseInteraction=2, Scroll=3,
            ViewportResize=4, Input=5, TouchMove=6, MediaInteraction=7,
            StyleSheetRule=8, CanvasMutation=9, Font=10, Log=11,
            Drag=12, StyleDeclaration=13, Selection=14.
        """
        tasks: List[BenchmarkTask] = []
        seen: Set[str] = set()
        index = 0
        current_url = ""

        RRWEB_INCREMENTAL = 3
        RRWEB_META = 4
        MOUSE_INTERACTION_SOURCE = 2
        INPUT_SOURCE = 5

        # Mouse interaction types
        MOUSE_CLICK = 2  # Click
        MOUSE_DBLCLICK = 4  # DblClick

        for event in events:
            try:
                event_type = event.get("type")
                data = event.get("data", {})

                # Meta events carry the page href
                if event_type == RRWEB_META:
                    new_url = data.get("href", "")
                    if new_url and new_url != current_url:
                        prev_url = current_url
                        current_url = new_url
                        if prev_url:
                            task = self._rrweb_navigation_to_task(
                                current_url, index, seen,
                            )
                            if task:
                                tasks.append(task)
                                index += 1
                    continue

                if event_type != RRWEB_INCREMENTAL:
                    continue

                source = data.get("source")

                # Mouse click interactions
                if source == MOUSE_INTERACTION_SOURCE:
                    interaction_type = data.get("type", -1)
                    if interaction_type in (MOUSE_CLICK, MOUSE_DBLCLICK):
                        node_id = data.get("id")
                        task = self._rrweb_click_to_task(
                            node_id, current_url, index, seen,
                        )
                        if task:
                            tasks.append(task)
                            index += 1

                # Input / form interactions
                elif source == INPUT_SOURCE:
                    node_id = data.get("id")
                    text = data.get("text", "")
                    is_checked = data.get("isChecked")
                    task = self._rrweb_input_to_task(
                        node_id, text, is_checked, current_url, index, seen,
                    )
                    if task:
                        tasks.append(task)
                        index += 1

            except Exception as exc:
                logger.warning("Skipping rrweb event due to error: %s", exc)

        logger.info("Ingested %d tasks from rrweb events (%d events)", len(tasks), len(events))
        return tasks

    # ------------------------------------------------------------------ #
    #  Helpers
    # ------------------------------------------------------------------ #

    @staticmethod
    def _url_to_app_id(url: str) -> str:
        """Extract domain as app_id from a URL."""
        try:
            parsed = urlparse(url)
            host = parsed.hostname or ""
            # Strip common prefixes
            for prefix in ("www.", "app.", "dashboard."):
                if host.startswith(prefix):
                    host = host[len(prefix):]
            return host or "unknown"
        except Exception:
            return "unknown"

    @staticmethod
    def _generate_task_id(prefix: str, index: int) -> str:
        """Generate a zero-padded task id, e.g. 'posthog-click-001'."""
        return f"{prefix}-{index:03d}"

    @staticmethod
    def _classify_bucket(event_type: str, url: str) -> TaskBucket:
        """Classify an event into a TaskBucket."""
        event_lower = event_type.lower()

        if event_lower in ("login", "auth", "signin", "signup", "register"):
            return TaskBucket.LOGIN_AUTH

        if event_lower in ("submit", "form", "post", "change", "input"):
            return TaskBucket.FORM_SUBMIT

        if event_lower in ("error", "exception", "4xx", "5xx", "retry"):
            return TaskBucket.ERROR_RETRY

        if event_lower in ("screenshot", "visual", "render", "paint"):
            return TaskBucket.VISUAL_UI

        # Check URL patterns for auth-related pages
        url_lower = (url or "").lower()
        if any(kw in url_lower for kw in ("/login", "/signin", "/auth", "/register", "/signup")):
            return TaskBucket.LOGIN_AUTH

        return TaskBucket.NAVIGATION_STATE

    @staticmethod
    def _dedup_key(url: str, action: str) -> str:
        """Create a deduplication key from URL + action."""
        raw = f"{url}|{action}"
        return hashlib.md5(raw.encode()).hexdigest()

    @staticmethod
    def _safe_list(data: dict, key: str, fallback_key: Optional[str] = None) -> list:
        """Safely extract a list from a dict, with optional fallback key."""
        result = data.get(key)
        if isinstance(result, list):
            return result
        if fallback_key:
            result = data.get(fallback_key)
            if isinstance(result, list):
                return result
        return []

    @staticmethod
    def _element_label(elements: list) -> str:
        """Extract a human-readable label from PostHog $elements list."""
        if not elements:
            return "element"
        el = elements[0]
        text = el.get("$el_text", el.get("text", "")).strip()
        tag = el.get("tag_name", "element")
        attrs = el.get("attributes", {}) or {}
        aria_label = attrs.get("attr__aria-label", "")
        placeholder = attrs.get("attr__placeholder", "")

        if text:
            return f'{tag} "{text}"'
        if aria_label:
            return f'{tag} "{aria_label}"'
        if placeholder:
            return f'{tag} with placeholder "{placeholder}"'
        el_id = attrs.get("attr__id", "")
        if el_id:
            return f"{tag}#{el_id}"
        return tag

    # ------------------------------------------------------------------ #
    #  PostHog task builders
    # ------------------------------------------------------------------ #

    def _posthog_autocapture_to_task(
        self,
        event_type: str,
        elements: list,
        url: str,
        index: int,
        seen: Set[str],
    ) -> Optional[BenchmarkTask]:
        dedup = self._dedup_key(url, f"autocapture-{event_type}")
        if dedup in seen:
            return None
        seen.add(dedup)

        label = self._element_label(elements)
        bucket = self._classify_bucket(event_type, url)
        app_id = self._url_to_app_id(url)

        action_verb = {
            "click": "Click on",
            "change": "Change the value of",
            "submit": "Submit the form via",
        }.get(event_type, f"Interact with ({event_type})")

        return BenchmarkTask(
            task_id=self._generate_task_id(f"posthog-{event_type}", index),
            app_id=app_id,
            bucket=bucket,
            prompt=f"{action_verb} the {label} on {url}",
            expected_outcome=f"The {event_type} action on {label} completes successfully",
            required_evidence=["screenshot", "trace"],
            pass_rule=f"Element {label} is interacted with via {event_type}",
            base_url=url,
            setup_steps=[f"Navigate to {url}"],
            teardown_steps=[],
        )

    def _posthog_pageview_to_task(
        self,
        url: str,
        index: int,
        seen: Set[str],
    ) -> Optional[BenchmarkTask]:
        dedup = self._dedup_key(url, "pageview")
        if dedup in seen:
            return None
        seen.add(dedup)

        app_id = self._url_to_app_id(url)
        bucket = self._classify_bucket("navigation", url)

        return BenchmarkTask(
            task_id=self._generate_task_id("posthog-nav", index),
            app_id=app_id,
            bucket=bucket,
            prompt=f"Navigate to {url} and verify the page loads",
            expected_outcome="Page loads without errors",
            required_evidence=["screenshot"],
            pass_rule="Page responds with HTTP 200 and renders content",
            base_url=url,
            setup_steps=[],
            teardown_steps=[],
        )

    def _posthog_error_to_task(
        self,
        properties: dict,
        url: str,
        index: int,
        seen: Set[str],
    ) -> Optional[BenchmarkTask]:
        message = properties.get("$exception_message", properties.get("message", "unknown error"))
        dedup = self._dedup_key(url, f"error-{message}")
        if dedup in seen:
            return None
        seen.add(dedup)

        app_id = self._url_to_app_id(url)

        return BenchmarkTask(
            task_id=self._generate_task_id("posthog-error", index),
            app_id=app_id,
            bucket=TaskBucket.ERROR_RETRY,
            prompt=f"Navigate to {url} and verify the error '{message}' is handled or resolved",
            expected_outcome="The page loads without the reported error",
            required_evidence=["screenshot", "trace"],
            pass_rule="No unhandled exceptions matching the original error",
            base_url=url,
            setup_steps=[f"Navigate to {url}"],
            teardown_steps=[],
        )

    # ------------------------------------------------------------------ #
    #  HAR task builders
    # ------------------------------------------------------------------ #

    def _har_error_to_task(
        self,
        url: str,
        method: str,
        status: int,
        index: int,
        seen: Set[str],
    ) -> Optional[BenchmarkTask]:
        dedup = self._dedup_key(url, f"error-{status}")
        if dedup in seen:
            return None
        seen.add(dedup)

        app_id = self._url_to_app_id(url)

        return BenchmarkTask(
            task_id=self._generate_task_id("har-error", index),
            app_id=app_id,
            bucket=TaskBucket.ERROR_RETRY,
            prompt=f"Reproduce and verify handling of HTTP {status} on {method} {url}",
            expected_outcome=f"The {method} request to {url} is retried or error is handled gracefully",
            required_evidence=["trace"],
            pass_rule=f"Response status is no longer {status} or error is handled",
            base_url=url,
            setup_steps=[f"Navigate to {self._url_to_base(url)}"],
            teardown_steps=[],
        )

    def _har_form_to_task(
        self,
        url: str,
        method: str,
        request: dict,
        index: int,
        seen: Set[str],
    ) -> Optional[BenchmarkTask]:
        dedup = self._dedup_key(url, f"form-{method}")
        if dedup in seen:
            return None
        seen.add(dedup)

        app_id = self._url_to_app_id(url)
        bucket = self._classify_bucket("submit", url)

        # Extract form field names from POST data if available
        post_data = request.get("postData", {})
        params = post_data.get("params", [])
        field_names = [p.get("name", "") for p in params if p.get("name")]
        field_desc = f" with fields: {', '.join(field_names)}" if field_names else ""

        return BenchmarkTask(
            task_id=self._generate_task_id("har-form", index),
            app_id=app_id,
            bucket=bucket,
            prompt=f"Submit a {method} request to {url}{field_desc}",
            expected_outcome=f"The {method} request completes with a success status",
            required_evidence=["trace"],
            pass_rule="Response status is 2xx after form submission",
            base_url=url,
            setup_steps=[f"Navigate to {self._url_to_base(url)}"],
            teardown_steps=[],
        )

    def _har_navigation_to_task(
        self,
        url: str,
        index: int,
        seen: Set[str],
    ) -> Optional[BenchmarkTask]:
        dedup = self._dedup_key(url, "navigation")
        if dedup in seen:
            return None
        seen.add(dedup)

        app_id = self._url_to_app_id(url)
        bucket = self._classify_bucket("navigation", url)

        return BenchmarkTask(
            task_id=self._generate_task_id("har-nav", index),
            app_id=app_id,
            bucket=bucket,
            prompt=f"Navigate to {url} and verify the page loads successfully",
            expected_outcome="Page loads with HTTP 200",
            required_evidence=["screenshot"],
            pass_rule="Page responds with HTTP 200 and renders content",
            base_url=url,
            setup_steps=[],
            teardown_steps=[],
        )

    # ------------------------------------------------------------------ #
    #  rrweb task builders
    # ------------------------------------------------------------------ #

    def _rrweb_navigation_to_task(
        self,
        url: str,
        index: int,
        seen: Set[str],
    ) -> Optional[BenchmarkTask]:
        dedup = self._dedup_key(url, "rrweb-nav")
        if dedup in seen:
            return None
        seen.add(dedup)

        app_id = self._url_to_app_id(url)
        bucket = self._classify_bucket("navigation", url)

        return BenchmarkTask(
            task_id=self._generate_task_id("rrweb-nav", index),
            app_id=app_id,
            bucket=bucket,
            prompt=f"Navigate to {url} and verify the page loads",
            expected_outcome="Page loads without errors",
            required_evidence=["screenshot"],
            pass_rule="Page responds with HTTP 200 and renders content",
            base_url=url,
            setup_steps=[],
            teardown_steps=[],
        )

    def _rrweb_click_to_task(
        self,
        node_id: Optional[int],
        url: str,
        index: int,
        seen: Set[str],
    ) -> Optional[BenchmarkTask]:
        target = f"node#{node_id}" if node_id else "element"
        dedup = self._dedup_key(url, f"rrweb-click-{node_id}")
        if dedup in seen:
            return None
        seen.add(dedup)

        app_id = self._url_to_app_id(url)
        bucket = self._classify_bucket("click", url)

        return BenchmarkTask(
            task_id=self._generate_task_id("rrweb-click", index),
            app_id=app_id,
            bucket=bucket,
            prompt=f"Click on {target} on {url}",
            expected_outcome=f"The click on {target} triggers the expected interaction",
            required_evidence=["screenshot", "trace"],
            pass_rule=f"Element {target} is clicked and page state updates",
            base_url=url,
            setup_steps=[f"Navigate to {url}"],
            teardown_steps=[],
        )

    def _rrweb_input_to_task(
        self,
        node_id: Optional[int],
        text: str,
        is_checked: Optional[bool],
        url: str,
        index: int,
        seen: Set[str],
    ) -> Optional[BenchmarkTask]:
        target = f"input#{node_id}" if node_id else "input"
        dedup = self._dedup_key(url, f"rrweb-input-{node_id}")
        if dedup in seen:
            return None
        seen.add(dedup)

        app_id = self._url_to_app_id(url)
        bucket = self._classify_bucket("input", url)

        if is_checked is not None:
            action = f"Toggle checkbox {target} to {'checked' if is_checked else 'unchecked'}"
        elif text:
            # Mask potentially sensitive input values
            masked = text if len(text) <= 3 else text[:2] + "***"
            action = f"Enter value '{masked}' into {target}"
        else:
            action = f"Interact with {target}"

        return BenchmarkTask(
            task_id=self._generate_task_id("rrweb-input", index),
            app_id=app_id,
            bucket=bucket,
            prompt=f"{action} on {url}",
            expected_outcome=f"The input on {target} is accepted",
            required_evidence=["screenshot", "trace"],
            pass_rule=f"Input field {target} contains the expected value",
            base_url=url,
            setup_steps=[f"Navigate to {url}"],
            teardown_steps=[],
        )

    # ------------------------------------------------------------------ #
    #  URL utilities
    # ------------------------------------------------------------------ #

    @staticmethod
    def _url_to_base(url: str) -> str:
        """Return scheme + host portion of a URL."""
        try:
            parsed = urlparse(url)
            return f"{parsed.scheme}://{parsed.netloc}"
        except Exception:
            return url
