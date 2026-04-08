"""
Device Testing Agent Tools

Unified tools for test execution, bug reproduction, exploration, and device control.
"""

import json
import logging
import os
from typing import Dict, Any, List

logger = logging.getLogger(__name__)


def create_device_testing_tools(service_ref):
    """
    Create unified device testing tools with service reference.

    Args:
        service_ref: Reference to AIAgentService (coordinator service)

    Returns:
        Dictionary of all device testing tool functions
    """

    # ========================================================================
    # Emulator Management Tools
    # ========================================================================

    async def launch_emulators(
        count: int = 1,
        avd_name: str = None,
        wait_for_boot: bool = False
    ) -> str:
        """
        Launch Android emulator(s) - intelligently checks if enough devices are already available.

        Args:
            count: Number of emulators needed (1-20, default: 1)
            avd_name: Optional AVD name (auto-selected if not provided)
            wait_for_boot: Whether to wait for emulators to boot (default: False)

        Returns:
            JSON string with launch results
        """
        try:
            import httpx

            # First, check how many devices are already available
            async with httpx.AsyncClient(timeout=30.0) as client:
                devices_response = await client.get(f"{os.environ.get('TA_BACKEND_URL', 'http://localhost:8000')}/api/device-simulation/devices")

                if devices_response.status_code == 200:
                    devices_data = devices_response.json()
                    android_devices = devices_data.get("devices", {}).get("android", [])
                    available_count = len(android_devices)

                    # If we already have enough devices, don't launch more
                    if available_count >= count:
                        return json.dumps({
                            "success": True,
                            "launched": [],
                            "count": 0,
                            "message": f"No need to launch new emulators. {available_count} device(s) already available (requested: {count})",
                            "available_devices": [d.get("device_id") for d in android_devices],
                            "skipped": True
                        })

                    # Calculate how many more we need
                    needed = count - available_count
                    logger.info(f"Need {needed} more emulators ({available_count} already available, {count} requested)")
                else:
                    # If we can't check devices, proceed with full launch
                    needed = count
                    available_count = 0

            # Launch only the needed number of emulators
            params = {
                "count": min(max(needed, 1), 20),
                "wait_for_boot": wait_for_boot
            }
            if avd_name:
                params["avd_name"] = avd_name

            async with httpx.AsyncClient(timeout=300.0) as client:
                response = await client.post(
                    f"{os.environ.get('TA_BACKEND_URL', 'http://localhost:8000')}/api/device-simulation/emulators/launch",
                    params=params
                )

                if response.status_code == 200:
                    result = response.json()
                    launched_count = len(result.get("launched", []))
                    total_available = available_count + launched_count

                    return json.dumps({
                        "success": True,
                        "launched": result.get("launched", []),
                        "count": launched_count,
                        "message": f"Launched {launched_count} new emulator(s). Total available: {total_available} (requested: {count})",
                        "details": result,
                        "previously_available": available_count,
                        "total_available": total_available
                    })
                else:
                    return json.dumps({
                        "success": False,
                        "error": f"Failed to launch emulators: HTTP {response.status_code}",
                        "details": response.text
                    })

        except Exception as e:
            logger.error(f"Error launching emulators: {e}")
            return json.dumps({
                "success": False,
                "error": str(e)
            })

    # ========================================================================
    # Test Execution & Bug Reproduction Tools
    # ========================================================================

    async def execute_test_scenario(
        scenario_name: str,
        device_id: str,
        test_steps_json: str = ""
    ) -> Dict[str, Any]:
        """Execute a predefined test scenario on a device.

        Args:
            scenario_name: Name of the test scenario to execute
            device_id: Device ID (e.g., "emulator-5554")
            test_steps_json: Optional JSON list of steps (if not provided, will use scenario from capabilities)

        Returns:
            Test execution results with screenshots and evidence
        """
        try:
            logger.info(f"Executing test scenario: {scenario_name} on device: {device_id}")

            # Determine steps: prefer JSON override, otherwise fall back to known scenarios in capabilities.
            test_steps = None
            if test_steps_json:
                try:
                    parsed = json.loads(test_steps_json)
                except Exception as e:
                    return {
                        "status": "error",
                        "message": f"Invalid test_steps_json: {e}",
                        "scenario_name": scenario_name,
                        "device_id": device_id,
                    }

                if isinstance(parsed, list):
                    test_steps = parsed
                elif isinstance(parsed, dict) and isinstance(parsed.get("steps"), list):
                    # Allow {"steps": [...]} payloads.
                    test_steps = parsed.get("steps")
                else:
                    return {
                        "status": "error",
                        "message": "test_steps_json must be a JSON array (or an object with a 'steps' array)",
                        "scenario_name": scenario_name,
                        "device_id": device_id,
                    }

            if not test_steps:
                scenarios = service_ref.capabilities.get("instagram_test_scenarios", {})
                scenario = scenarios.get(scenario_name)
                if not scenario:
                    return {
                        "status": "error",
                        "message": f"Scenario '{scenario_name}' not found in capabilities",
                        "available_scenarios": list(scenarios.keys()),
                    }
                test_steps = scenario.get("steps", [])

            if not test_steps:
                return {
                    "status": "error",
                    "message": "No test steps provided or found for scenario",
                    "scenario_name": scenario_name,
                    "device_id": device_id,
                }

            # Use unified bug reproduction service to execute scenario
            from ..bug_reproduction_service import TestScenarioInput

            appium_mcp = getattr(service_ref, "appium_mcp", None)
            bug_repro_service = getattr(service_ref, "bug_repro_service", None)
            if bug_repro_service is None:
                return {
                    "status": "error",
                    "message": "Bug reproduction service is not available",
                    "scenario_name": scenario_name,
                    "device_id": device_id,
                }

            session_id = None
            try:
                # Ensure a Mobile MCP session exists (idempotent for a device)
                if appium_mcp is not None:
                    session_id = await appium_mcp.create_session(
                        device_id=device_id,
                        enable_streaming=False,
                        fps=2,
                    )

                scenario_input = TestScenarioInput(
                    scenario_name=scenario_name,
                    device_id=device_id,
                    test_steps=test_steps,
                    session_id=session_id,
                )

                result = await bug_repro_service.execute_scenario(
                    scenario=scenario_input,
                    mcp_client=getattr(appium_mcp, "mcp_client", None),
                )
            finally:
                if session_id and appium_mcp is not None:
                    try:
                        await appium_mcp.close_session(session_id)
                    except Exception:
                        # Best-effort cleanup
                        pass

            # Normalize output
            if hasattr(result, "model_dump"):
                return result.model_dump()
            return result
        except Exception as e:
            logger.error(f"Error executing test scenario: {e}")
            return {
                "status": "error",
                "message": str(e),
                "scenario_name": scenario_name,
                "device_id": device_id,
            }

    async def reproduce_bug(
        bug_description: str,
        device_id: str,
        manual_steps: List[str],
        device_info_json: str = ""
    ) -> Dict[str, Any]:
        """
        Reproduce a bug from a manual bug report.

        Args:
            bug_description: Description of the bug
            device_id: Device ID (e.g., "emulator-5554")
            manual_steps: List of manual reproduction steps (natural language)
            device_info: Optional device information

        Returns:
            Bug reproduction results with evidence
        """
        try:
            logger.info(f"Reproducing bug: {bug_description} on device: {device_id}")

            from ..bug_reproduction_service import BugReportInput
            from ..infrastructure import MCPAppiumClient, Platform

            # Create bug report input
            bug_report = BugReportInput(
                title=bug_description,
                description=bug_description,
                reproduction_steps=manual_steps,
                expected_behavior="App should work correctly",
                actual_behavior="Bug occurs",
                device_id=device_id,
                severity="medium"
            )

            # Create MCP client for bug reproduction
            mcp_client = MCPAppiumClient(platform=Platform.ANDROID)

            # Generate a unique session ID for this reproduction attempt
            import uuid
            session_id = f"repro-{uuid.uuid4().hex[:12]}"

            # Execute bug reproduction
            result = await service_ref.bug_repro_service.reproduce_bug(
                bug_report=bug_report,
                mcp_client=mcp_client,
                session_id=session_id,
            )

            return result.model_dump()

        except Exception as e:
            logger.error(f"Error reproducing bug: {e}")
            return {
                "status": "error",
                "message": str(e),
                "bug_description": bug_description,
                "device_id": device_id
            }

    async def get_execution_status(execution_id: str = None) -> Dict[str, Any]:
        """
        Get the status of a test execution or bug reproduction.

        Args:
            execution_id: Optional execution ID (if not provided, returns all)

        Returns:
            Execution status information
        """
        # TODO: Implement execution status tracking
        return {
            "status": "not_implemented",
            "message": "Execution status tracking not yet implemented",
            "execution_id": execution_id
        }

    # ========================================================================
    # Golden Bug Evaluation Tools
    # ========================================================================

    async def list_golden_bugs() -> Dict[str, Any]:
        """List all configured golden bugs for deterministic evaluation."""
        try:
            if not hasattr(service_ref, "golden_bug_service") or service_ref.golden_bug_service is None:
                return {
                    "status": "error",
                    "message": "GoldenBugService is not configured on AIAgentService",
                }

            summaries = service_ref.golden_bug_service.list_golden_bug_summaries()
            return {
                "status": "ok",
                "count": len(summaries),
                "golden_bugs": [s.model_dump() for s in summaries],
            }
        except Exception as e:
            logger.error(f"Error listing golden bugs: {e}")
            return {
                "status": "error",
                "message": str(e),
            }

    async def run_golden_bug(
        bug_id: str,
        device_id_override: str = "",
        max_attempts: int = 3,
    ) -> Dict[str, Any]:
        """Run a single golden bug using the golden bug evaluation service.

        Args:
            bug_id: Golden bug ID (e.g., 'GOLDEN-001')
            device_id_override: Optional device ID override
            max_attempts: Maximum number of attempts (default: 3)

        Returns:
            Full GoldenBugRunResult as a JSON-serializable dict.
        """
        try:
            if not hasattr(service_ref, "golden_bug_service") or service_ref.golden_bug_service is None:
                return {
                    "status": "error",
                    "message": "GoldenBugService is not configured on AIAgentService",
                    "bug_id": bug_id,
                }

            run = await service_ref.golden_bug_service.run_golden_bug(
                bug_id=bug_id,
                device_id_override=device_id_override or None,
                max_attempts=max_attempts,
            )
            data = run.model_dump()

            # Convenience summary plus explicit screenshot hint for DevMate inspector
            attempts = data.get("attempts") or []
            last_attempt = attempts[-1] if attempts else None
            screenshot_url = last_attempt.get("screenshot_url") if last_attempt else None
            if screenshot_url:
                data["summary"] = (
                    f"Screenshot saved to: {screenshot_url} "
                    f"(golden bug '{bug_id}' run completed)"
                )
            else:
                data["summary"] = f"Golden bug '{bug_id}' run completed."
            return data
        except Exception as e:
            logger.error(f"Error running golden bug {bug_id}: {e}")
            return {
                "status": "error",
                "message": str(e),
                "bug_id": bug_id,
            }

    async def run_all_golden_bugs(
        device_id_override: str = "",
        max_attempts: int = 3,
    ) -> Dict[str, Any]:
        """Run the full golden bug suite and return evaluation metrics.

        Args:
            device_id_override: Optional device ID override applied to all bugs
            max_attempts: Maximum attempts per bug (default: 3)

        Returns:
            GoldenBugEvaluationReport as a JSON-serializable dict.
        """
        try:
            if not hasattr(service_ref, "golden_bug_service") or service_ref.golden_bug_service is None:
                return {
                    "status": "error",
                    "message": "GoldenBugService is not configured on AIAgentService",
                }

            report = await service_ref.golden_bug_service.run_all_golden_bugs(
                device_id_override=device_id_override or None,
                max_attempts=max_attempts,
            )
            data = report.model_dump()

            # Optional convenience summary + at least one screenshot reference
            latest_screenshot = None
            for run in data.get("runs", []):
                attempts = run.get("attempts") or []
                if attempts:
                    last_attempt = attempts[-1]
                    if last_attempt.get("screenshot_url"):
                        latest_screenshot = last_attempt["screenshot_url"]

            if latest_screenshot:
                data["summary"] = (
                    f"Screenshot saved to: {latest_screenshot} "
                    f"(golden evaluation run '{data.get('run_id')}' "
                    f"for {data.get('metrics', {}).get('total_bugs', 0)} bugs)"
                )
            else:
                data["summary"] = (
                    f"Golden evaluation run '{data.get('run_id')}' completed for "
                    f"{data.get('metrics', {}).get('total_bugs', 0)} bugs."
                )
            return data
        except Exception as e:
            logger.error(f"Error running all golden bugs: {e}")
            return {
                "status": "error",
                "message": str(e),
            }

    # ========================================================================
    # Autonomous Exploration Tools
    # ========================================================================

    async def start_autonomous_exploration(
        device_id: str,
        max_steps: int = 20,
        strategy: str = "comprehensive"
    ) -> str:
        """
        Start autonomous exploration of an Android device with structured reporting.

        Args:
            device_id: Device ID to explore (e.g., 'emulator-5554')
            max_steps: Maximum number of exploration steps (default: 20)
            strategy: Exploration strategy - 'comprehensive', 'quick', or 'targeted' (default: 'comprehensive')

        Returns:
            JSON string with exploration_id and status
        """
        try:
            exploration_id = await service_ref.exploration_service.start_exploration(
                device_id=device_id,
                max_steps=max_steps,
                exploration_strategy=strategy
            )
            return json.dumps({
                "success": True,
                "exploration_id": exploration_id,
                "device_id": device_id,
                "max_steps": max_steps,
                "strategy": strategy,
                "message": f"Started autonomous exploration of {device_id}. Exploration ID: {exploration_id}"
            })
        except Exception as e:
            logger.error(f"Error starting exploration: {e}")
            return json.dumps({"error": str(e)})

    async def get_exploration_report(exploration_id: str) -> str:
        """
        Get the current exploration report with thought-action-observation steps.

        Args:
            exploration_id: The exploration ID returned from start_autonomous_exploration

        Returns:
            JSON string with complete exploration report including all steps
        """
        try:
            report = service_ref.exploration_service.get_exploration_report(exploration_id)
            if report:
                return json.dumps(report.model_dump(), indent=2)
            else:
                return json.dumps({"error": f"Exploration '{exploration_id}' not found"})
        except Exception as e:
            logger.error(f"Error getting exploration report: {e}")
            return json.dumps({"error": str(e)})

    async def list_explorations() -> str:
        """
        List all exploration sessions.

        Returns:
            JSON string with list of all explorations and their status
        """
        try:
            explorations = service_ref.exploration_service.list_explorations()
            return json.dumps([e.model_dump() for e in explorations], indent=2)
        except Exception as e:
            logger.error(f"Error listing explorations: {e}")
            return json.dumps({"error": str(e)})

    # ========================================================================
    # Device Discovery Tool
    # ========================================================================

    async def list_available_devices() -> str:
        """
        List all available devices (emulators and physical devices).

        Use this tool FIRST before asking the user for device IDs.
        This will show you all connected devices automatically.

        Returns:
            JSON string with list of available devices and their details
        """
        try:
            # Use Mobile MCP client to list devices
            if hasattr(service_ref, 'mcp_client') and service_ref.mcp_client:
                devices_str = await service_ref.mcp_client.list_available_devices()
                return devices_str
            else:
                # Fallback to adb devices
                import subprocess
                result = subprocess.run(
                    ['adb', 'devices', '-l'],
                    capture_output=True,
                    text=True,
                    timeout=5
                )

                devices = []
                for line in result.stdout.split('\n')[1:]:
                    if 'device' in line:
                        parts = line.split()
                        if len(parts) >= 2:
                            device_id = parts[0]
                            status = parts[1]
                            devices.append({
                                "device_id": device_id,
                                "status": status,
                                "type": "emulator" if "emulator" in device_id else "physical"
                            })

                return json.dumps({
                    "devices": devices,
                    "count": len(devices)
                }, indent=2)
        except Exception as e:
            logger.error(f"Error listing devices: {e}")
            return json.dumps({"error": str(e)})

    # ========================================================================
    # Manual Device Control Tools (imported from infrastructure)
    # ========================================================================

    from ..infrastructure import (
        find_elements_on_device,
        click_element_by_text,
        execute_device_action
    )

    # Return all tools
    return {
        # Emulator management
        "launch_emulators": launch_emulators,
        # Device discovery
        "list_available_devices": list_available_devices,
        # Test execution & bug reproduction
        "execute_test_scenario": execute_test_scenario,
        "reproduce_bug": reproduce_bug,
        "get_execution_status": get_execution_status,
        # Golden bug evaluation
        "list_golden_bugs": list_golden_bugs,
        "run_golden_bug": run_golden_bug,
        "run_all_golden_bugs": run_all_golden_bugs,
        # Autonomous exploration
        "start_autonomous_exploration": start_autonomous_exploration,
        "get_exploration_report": get_exploration_report,
        "list_explorations": list_explorations,
        # Manual device control
        "find_elements_on_device": find_elements_on_device,
        "click_element_by_text": click_element_by_text,
        "execute_device_action": execute_device_action,
    }


__all__ = ["create_device_testing_tools"]

