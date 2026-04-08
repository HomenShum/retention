"""
Agentic Vision Tools — GPT-5.4 Code Execution + SoM Integration

These tools wrap the AgenticVisionClient to provide clean interfaces for
the device testing agent.  Each tool follows a two-layer pipeline:

  Layer 1 — SoM Structural Annotation (deterministic, <100ms, free)
    Accessibility tree → element classification → structured element list
  Layer 2 — GPT-5.4 Agentic Vision (intelligent, Think-Act-Observe)
    Screenshot + SoM element list → GPT-5.4 generates Python code →
    LocalCodeExecutor runs it → results fed back for next iteration

Tools:
- zoom_and_inspect: Crop and zoom to inspect fine-grained details
- annotate_screen: Draw bounding boxes and labels to ground reasoning
- visual_math: Extract data from tables/charts and run calculations
- multi_step_vision: Full Think-Act-Observe pipeline for complex tasks
"""

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from .autonomous_navigation_tools import _classify_element_type, _ELEMENT_PALETTE

logger = logging.getLogger(__name__)


def _build_som_element_list(raw_elements: list) -> List[Dict[str, Any]]:
    """
    Convert raw accessibility-tree elements into a compact SoM element list.

    Each entry has: idx, type (tag), label, x, y, width, height.
    Containers (FrameLayout, LinearLayout, RelativeLayout) are filtered out.
    """
    _CONTAINER_SUBSTRINGS = {"framelayout", "linearlayout", "relativelayout", "constraintlayout"}
    som: List[Dict[str, Any]] = []
    idx = 0

    for elem in raw_elements:
        class_name = (elem.get("class") or elem.get("type") or "").lower()
        if any(c in class_name for c in _CONTAINER_SUBSTRINGS):
            continue

        etype = _classify_element_type(elem)
        _, _, tag = _ELEMENT_PALETTE.get(etype, _ELEMENT_PALETTE["unknown"])

        # Coordinates: support both MCP nested and ADB flat layouts
        coords = elem.get("coordinates", {})
        x = int(coords.get("x", 0) or elem.get("x", 0) or 0)
        y = int(coords.get("y", 0) or elem.get("y", 0) or 0)
        w = int(coords.get("width", 0) or elem.get("width", 0) or 0)
        h = int(coords.get("height", 0) or elem.get("height", 0) or 0)

        label = (
            elem.get("label") or elem.get("text") or elem.get("name")
            or elem.get("content_desc") or ""
        ).strip()
        if not label:
            rid = elem.get("resource_id") or elem.get("identifier") or ""
            if rid:
                label = rid.split("/")[-1].replace("_", " ")

        idx += 1
        som.append({
            "idx": idx,
            "type": tag,
            "label": label,
            "x": x, "y": y, "width": w, "height": h,
        })

    return som


def create_agentic_vision_tools(
    mobile_mcp_client,
    device_id: str,
    screenshot_dir: Optional[Path] = None
) -> Dict[str, Any]:
    """
    Create agentic vision tools that wrap the AgenticVisionClient.
    
    Args:
        mobile_mcp_client: MobileMCPClient instance for taking screenshots
        device_id: Target device ID
        screenshot_dir: Directory to save screenshots (default: backend/screenshots)
        
    Returns:
        Dictionary of tool functions
    """
    if screenshot_dir is None:
        screenshot_dir = Path(__file__).resolve().parents[4] / "screenshots"
    screenshot_dir.mkdir(parents=True, exist_ok=True)
    
    async def _get_screenshot_and_som(device_id: str, suffix: str):
        """Shared helper: take screenshot + get elements → SoM list."""
        import base64
        from datetime import datetime

        result = await mobile_mcp_client.take_screenshot(device_id)
        if not (isinstance(result, dict) and result.get("type") == "image"):
            return None, None, None, "Failed to capture screenshot: unexpected format"

        base64_data = result.get("data", "")
        if not base64_data:
            return None, None, None, "Failed to capture screenshot - no image data"

        image_bytes = base64.b64decode(base64_data)

        # Save original screenshot
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filepath = screenshot_dir / f"{device_id}_{timestamp}_{suffix}.png"
        filepath.write_bytes(image_bytes)

        # Get accessibility tree elements → build SoM list
        som_elements: List[Dict[str, Any]] = []
        try:
            elements_result = await mobile_mcp_client.list_elements_on_screen(device_id)
            raw_elements = elements_result if isinstance(elements_result, list) else []
            som_elements = _build_som_element_list(raw_elements)
            logger.info(f"📋 SoM: {len(som_elements)} elements from accessibility tree")
        except Exception as e:
            logger.warning(f"Could not get elements for SoM: {e}")

        return image_bytes, som_elements, filepath, None

    async def zoom_and_inspect(
        device_id: str = device_id,
        query: str = "Describe any small text or fine details in the image",
        zoom_targets: Optional[List[str]] = None,
    ) -> str:
        """
        Take a screenshot and use GPT-5.4 Agentic Vision to zoom and inspect details.

        Two-layer pipeline:
          1. SoM: Accessibility tree → structured element list
          2. GPT-5.4: Think-Act-Observe with code execution on the image

        Args:
            device_id: Device identifier (default: current device)
            query: What to look for (e.g., "Read the serial number")
            zoom_targets: Optional areas to focus on (e.g., ["top-right corner"])
        """
        try:
            from ..agentic_vision_service import AgenticVisionClient

            image_bytes, som_elements, filepath, err = await _get_screenshot_and_som(device_id, "agentic_zoom")
            if err:
                return err

            client = AgenticVisionClient()
            vision_result = await client.analyze_with_zooming(
                image_bytes, query, zoom_targets, som_elements=som_elements,
            )

            if vision_result.success:
                step_summary = ""
                if vision_result.steps:
                    step_summary = f"\n\n📊 Analysis Steps: {vision_result.total_steps}"
                    for i, step in enumerate(vision_result.steps, 1):
                        step_summary += f"\n  {i}. {step.action.value}: {step.reasoning[:50]}..."

                return (
                    f"🔍 Agentic Vision Analysis (Zoom & Inspect):\n\n"
                    f"{vision_result.final_analysis}{step_summary}\n\n"
                    f"📋 SoM elements: {len(som_elements or [])}\n"
                    f"📸 Screenshot saved: {filepath}"
                )
            return f"Agentic Vision failed: {vision_result.error}"

        except ImportError as e:
            logger.warning(f"Required package not available: {e}")
            return f"Agentic Vision missing dependencies: {e}. Check requirements.txt."
        except Exception as e:
            logger.error(f"Error in zoom_and_inspect: {e}")
            return f"Error: {e}"

    async def annotate_screen(
        device_id: str = device_id,
        query: str = "Identify and label all interactive elements",
        annotation_type: str = "counts",
    ) -> str:
        """
        Take a screenshot and use GPT-5.4 Agentic Vision to draw annotations.

        Args:
            device_id: Device identifier (default: current device)
            query: What to annotate (e.g., "Count all buttons")
            annotation_type: "bounding_boxes", "labels", or "counts"
        """
        try:
            from ..agentic_vision_service import AgenticVisionClient

            image_bytes, som_elements, filepath, err = await _get_screenshot_and_som(device_id, "agentic_annotate")
            if err:
                return err

            client = AgenticVisionClient()
            vision_result = await client.analyze_with_annotation(
                image_bytes, query, annotation_type, som_elements=som_elements,
            )

            if vision_result.success:
                images_info = ""
                if vision_result.images_generated > 0:
                    images_info = f"\n🖼️ Generated {vision_result.images_generated} annotated image(s)"

                return (
                    f"🎯 Agentic Vision Analysis (Annotate):\n\n"
                    f"{vision_result.final_analysis}{images_info}\n\n"
                    f"📋 SoM elements: {len(som_elements or [])}\n"
                    f"📸 Original screenshot: {filepath}"
                )
            return f"Agentic Vision failed: {vision_result.error}"

        except ImportError as e:
            logger.warning(f"Required package not available: {e}")
            return f"Agentic Vision missing dependencies: {e}."
        except Exception as e:
            logger.error(f"Error in annotate_screen: {e}")
            return f"Error: {e}"
    
    async def visual_math(
        device_id: str = device_id,
        query: str = "Extract all numerical data and calculate totals",
    ) -> str:
        """
        Take a screenshot and use GPT-5.4 Agentic Vision to extract data and calculate.

        Two-layer pipeline:
          1. SoM: Accessibility tree → structured element list
          2. GPT-5.4: Think-Act-Observe with deterministic Python calculations

        Args:
            device_id: Device identifier (default: current device)
            query: What to extract or calculate (e.g., "Sum all prices")
        """
        try:
            from ..agentic_vision_service import AgenticVisionClient

            image_bytes, som_elements, filepath, err = await _get_screenshot_and_som(device_id, "agentic_math")
            if err:
                return err

            client = AgenticVisionClient()
            vision_result = await client.analyze_visual_math(
                image_bytes, query, som_elements=som_elements,
            )

            if vision_result.success:
                code_info = ""
                if vision_result.steps:
                    for step in vision_result.steps:
                        if step.code_output:
                            code_info += f"\n\n📊 Calculation Output:\n```\n{step.code_output[:500]}\n```"

                return (
                    f"🔢 Agentic Vision Analysis (Visual Math):\n\n"
                    f"{vision_result.final_analysis}{code_info}\n\n"
                    f"📋 SoM elements: {len(som_elements or [])}\n"
                    f"📸 Screenshot saved: {filepath}"
                )
            return f"Agentic Vision failed: {vision_result.error}"

        except ImportError as e:
            logger.warning(f"Required package not available: {e}")
            return f"Agentic Vision missing dependencies: {e}."
        except Exception as e:
            logger.error(f"Error in visual_math: {e}")
            return f"Error: {e}"

    async def multi_step_vision(
        device_id: str = device_id,
        query: str = "Analyze this screen comprehensively",
        instructions: Optional[str] = None,
    ) -> str:
        """
        Take a screenshot and perform full multi-step GPT-5.4 agentic vision analysis.

        Two-layer pipeline:
          1. SoM: Accessibility tree → structured element list
          2. GPT-5.4: Full Think-Act-Observe loop with code execution

        Args:
            device_id: Device identifier (default: current device)
            query: Complex query requiring multi-step investigation
            instructions: Optional additional instructions for the analysis
        """
        try:
            from ..agentic_vision_service import AgenticVisionClient

            image_bytes, som_elements, filepath, err = await _get_screenshot_and_som(device_id, "agentic_multi")
            if err:
                return err

            client = AgenticVisionClient()
            vision_result = await client.multi_step_vision(
                image_bytes, query, instructions, som_elements=som_elements,
            )

            if vision_result.success:
                step_trace = ""
                if vision_result.steps:
                    step_trace = "\n\n🔄 Investigation Trace:"
                    for i, step in enumerate(vision_result.steps, 1):
                        step_trace += f"\n  Step {i}: {step.action.value}"
                        step_trace += f"\n    Reasoning: {step.reasoning[:80]}..."
                        if step.code_output:
                            step_trace += f"\n    Output: {step.code_output[:100]}..."

                return (
                    f"🧠 Agentic Vision Analysis (Multi-Step):\n\n"
                    f"{vision_result.final_analysis}{step_trace}\n\n"
                    f"📊 Total steps: {vision_result.total_steps}\n"
                    f"🖼️ Images generated: {vision_result.images_generated}\n"
                    f"📋 SoM elements: {len(som_elements or [])}\n"
                    f"📸 Original screenshot: {filepath}"
                )
            return f"Agentic Vision failed: {vision_result.error}"

        except ImportError as e:
            logger.warning(f"Required package not available: {e}")
            return f"Agentic Vision missing dependencies: {e}."
        except Exception as e:
            logger.error(f"Error in multi_step_vision: {e}")
            return f"Error: {e}"
    
    # Return all tools
    return {
        "zoom_and_inspect": zoom_and_inspect,
        "annotate_screen": annotate_screen,
        "visual_math": visual_math,
        "multi_step_vision": multi_step_vision,
    }


__all__ = ["create_agentic_vision_tools"]
