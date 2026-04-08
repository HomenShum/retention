"""
Multi-Surface Execution — KYB/AML, EHR, Legacy Portal flow runners.

Each surface defines:
  - A workflow family with known steps
  - Success/failure criteria
  - Expected screen states
  - Replay checkpoints

These run through the same trajectory replay engine as mobile QA,
but with surface-specific step definitions and validation logic.

Supported surfaces:
  - kyb_aml: KYB/AML entity extraction from public registries
  - ehr_scheduling: EHR appointment verification and scheduling
  - legacy_portal: Legacy freight portal invoice extraction
  - browser_checkout: Standard e-commerce checkout flow

Usage:
    from multi_surface import get_surface_config, create_surface_trajectory

    config = get_surface_config("kyb_aml")
    trajectory = create_surface_trajectory(config)
"""

import hashlib
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from ..device_testing.trajectory_logger import TrajectoryLog, TrajectoryStep


@dataclass
class SurfaceConfig:
    """Configuration for a specific workflow surface."""
    surface_id: str
    name: str
    family: str
    surface_type: str  # "browser" | "android" | "desktop" | "hybrid"
    description: str
    target_url: Optional[str] = None
    target_app: Optional[str] = None
    steps: List[Dict[str, Any]] = field(default_factory=list)
    success_criteria: List[str] = field(default_factory=list)
    failure_criteria: List[str] = field(default_factory=list)
    estimated_tokens_full: int = 0
    estimated_time_full_s: float = 0.0
    estimated_tokens_replay: int = 0
    estimated_time_replay_s: float = 0.0


# ── Surface Definitions ──────────────────────────────────────────────────────

KYB_AML_CONFIG = SurfaceConfig(
    surface_id="kyb_aml",
    name="KYB/AML Entity Extraction",
    family="kyb_aml",
    surface_type="browser",
    description="Extract entity data from public registries (SEC EDGAR, state registries), verify against sanctions lists",
    target_url="https://www.sec.gov/cgi-bin/browse-edgar",
    steps=[
        {"action": "Navigate to SEC EDGAR search", "semantic_label": "open_registry", "tool": "navigate", "params": {"url": "https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany"}},
        {"action": "Enter company name in search field", "semantic_label": "search_entity", "tool": "type_text", "params": {"selector": "#company", "text": "Acme Corp"}},
        {"action": "Submit search form", "semantic_label": "submit_search", "tool": "click", "params": {"selector": "input[type=submit]"}},
        {"action": "Select first result from filing list", "semantic_label": "select_result", "tool": "click", "params": {"selector": ".companyName a"}},
        {"action": "Extract CIK number and entity name", "semantic_label": "extract_cik", "tool": "extract_text", "params": {"selectors": [".companyName", ".CIK"]}},
        {"action": "Navigate to filing detail page", "semantic_label": "open_filing", "tool": "click", "params": {"selector": "a[href*='filing']"}},
        {"action": "Extract officer names and jurisdiction", "semantic_label": "extract_officers", "tool": "extract_text", "params": {"selectors": [".officer", ".jurisdiction"]}},
        {"action": "Cross-reference with OFAC sanctions list", "semantic_label": "check_sanctions", "tool": "navigate", "params": {"url": "https://sanctionssearch.ofac.treas.gov/"}},
        {"action": "Search entity on sanctions list", "semantic_label": "sanctions_search", "tool": "type_text", "params": {"selector": "#ctl00_MainContent_txtLastName", "text": "Acme Corp"}},
        {"action": "Verify no sanctions match", "semantic_label": "verify_clear", "tool": "assert_text", "params": {"expected": "No results found"}},
        {"action": "Generate case bundle with all extracted data", "semantic_label": "generate_bundle", "tool": "collect_evidence", "params": {}},
        {"action": "Record final status: entity verified", "semantic_label": "record_status", "tool": "emit_verdict", "params": {"verdict": "pass", "confidence": 0.95}},
    ],
    success_criteria=["CIK extracted", "No sanctions match", "Case bundle generated"],
    failure_criteria=["Entity not found", "Sanctions match detected", "Registry timeout"],
    estimated_tokens_full=28_000,
    estimated_time_full_s=95.0,
    estimated_tokens_replay=2_100,
    estimated_time_replay_s=12.0,
)

EHR_SCHEDULING_CONFIG = SurfaceConfig(
    surface_id="ehr_scheduling",
    name="EHR Appointment Verification",
    family="ehr_scheduling",
    surface_type="browser",
    description="Verify appointment scheduling, insurance eligibility, and payer metadata in EHR system",
    target_url="https://demo-ehr.example.com",
    steps=[
        {"action": "Login to EHR system", "semantic_label": "login", "tool": "type_text", "params": {"fields": {"username": "demo_user", "password": "***"}}},
        {"action": "Navigate to patient search", "semantic_label": "search_patient", "tool": "click", "params": {"selector": "#patientSearch"}},
        {"action": "Enter patient ID and search", "semantic_label": "find_patient", "tool": "type_text", "params": {"selector": "#patientId", "text": "PT-12345"}},
        {"action": "Open patient record", "semantic_label": "open_record", "tool": "click", "params": {"selector": ".patientResult:first-child"}},
        {"action": "Navigate to appointments tab", "semantic_label": "open_appointments", "tool": "click", "params": {"selector": "#appointmentsTab"}},
        {"action": "Verify upcoming appointment exists", "semantic_label": "verify_appointment", "tool": "assert_text", "params": {"expected": "Scheduled"}},
        {"action": "Check insurance eligibility status", "semantic_label": "check_insurance", "tool": "click", "params": {"selector": "#insuranceTab"}},
        {"action": "Verify insurance is active", "semantic_label": "verify_insurance", "tool": "assert_text", "params": {"expected": "Active"}},
        {"action": "Verify payer metadata matches", "semantic_label": "verify_payer", "tool": "extract_text", "params": {"selectors": [".payerName", ".planId", ".groupNumber"]}},
        {"action": "Confirm prior authorization if required", "semantic_label": "check_prior_auth", "tool": "conditional_check", "params": {"condition": "prior_auth_required"}},
        {"action": "Capture all verification screenshots", "semantic_label": "capture_evidence", "tool": "screenshot_sequence", "params": {"screens": ["appointment", "insurance", "payer"]}},
        {"action": "Generate verification report", "semantic_label": "generate_report", "tool": "emit_verdict", "params": {"verdict": "pass"}},
    ],
    success_criteria=["Appointment confirmed", "Insurance active", "Payer metadata verified"],
    failure_criteria=["Patient not found", "Insurance inactive", "Prior auth denied"],
    estimated_tokens_full=35_000,
    estimated_time_full_s=120.0,
    estimated_tokens_replay=2_800,
    estimated_time_replay_s=25.0,
)

LEGACY_PORTAL_CONFIG = SurfaceConfig(
    surface_id="legacy_portal",
    name="Legacy Freight Portal Invoice Extraction",
    family="legacy_portal",
    surface_type="browser",
    description="Navigate legacy freight portal with branching logic, extract invoice PDF, capture state transitions",
    target_url="https://freight-portal.example.com",
    steps=[
        {"action": "Login to freight portal", "semantic_label": "login", "tool": "type_text", "params": {"fields": {"user": "ops_user", "pass": "***"}}},
        {"action": "Navigate to shipment search", "semantic_label": "search_shipments", "tool": "click", "params": {"selector": "#shipmentSearch"}},
        {"action": "Enter BOL number", "semantic_label": "enter_bol", "tool": "type_text", "params": {"selector": "#bolNumber", "text": "BOL-2026-45678"}},
        {"action": "Select shipment from results", "semantic_label": "select_shipment", "tool": "click", "params": {"selector": "tr.shipmentRow:first-child"}},
        {"action": "Branch: check if invoice available", "semantic_label": "check_invoice_branch", "tool": "conditional_check", "params": {"condition": "invoice_link_present"}},
        {"action": "Download invoice PDF", "semantic_label": "download_invoice", "tool": "click", "params": {"selector": "a.invoiceDownload"}},
        {"action": "Verify PDF contains required fields", "semantic_label": "verify_pdf", "tool": "extract_pdf", "params": {"fields": ["total_amount", "shipper", "consignee", "weight"]}},
        {"action": "Navigate to tracking tab", "semantic_label": "open_tracking", "tool": "click", "params": {"selector": "#trackingTab"}},
        {"action": "Capture all tracking events", "semantic_label": "capture_tracking", "tool": "extract_text", "params": {"selector": ".trackingEvent"}},
        {"action": "Generate extraction bundle", "semantic_label": "generate_bundle", "tool": "collect_evidence", "params": {}},
    ],
    success_criteria=["Invoice PDF downloaded", "Required fields extracted", "Tracking events captured"],
    failure_criteria=["Shipment not found", "Invoice not available", "Portal timeout"],
    estimated_tokens_full=25_000,
    estimated_time_full_s=85.0,
    estimated_tokens_replay=1_500,
    estimated_time_replay_s=18.0,
)

BROWSER_CHECKOUT_CONFIG = SurfaceConfig(
    surface_id="browser_checkout",
    name="E-Commerce Checkout Flow",
    family="browser_checkout",
    surface_type="browser",
    description="Standard e-commerce checkout: search → cart → checkout → payment → confirmation",
    target_url="https://demo-store.example.com",
    steps=[
        {"action": "Navigate to store homepage", "semantic_label": "open_store", "tool": "navigate", "params": {"url": "https://demo-store.example.com"}},
        {"action": "Search for product", "semantic_label": "search_product", "tool": "type_text", "params": {"selector": "#searchInput", "text": "wireless headphones"}},
        {"action": "Select first product result", "semantic_label": "select_product", "tool": "click", "params": {"selector": ".productCard:first-child"}},
        {"action": "Add to cart", "semantic_label": "add_to_cart", "tool": "click", "params": {"selector": "#addToCart"}},
        {"action": "Open cart", "semantic_label": "open_cart", "tool": "click", "params": {"selector": "#cartIcon"}},
        {"action": "Proceed to checkout", "semantic_label": "checkout", "tool": "click", "params": {"selector": "#proceedToCheckout"}},
        {"action": "Fill shipping address", "semantic_label": "shipping_address", "tool": "type_text", "params": {"fields": {"address": "123 Test St", "city": "San Jose", "zip": "95134"}}},
        {"action": "Select payment method", "semantic_label": "payment_method", "tool": "click", "params": {"selector": "#creditCard"}},
        {"action": "Confirm order", "semantic_label": "confirm_order", "tool": "click", "params": {"selector": "#placeOrder"}},
        {"action": "Verify order confirmation", "semantic_label": "verify_confirmation", "tool": "assert_text", "params": {"expected": "Order Confirmed"}},
    ],
    success_criteria=["Order confirmed", "Confirmation number displayed"],
    failure_criteria=["Payment declined", "Out of stock", "Checkout error"],
    estimated_tokens_full=22_000,
    estimated_time_full_s=75.0,
    estimated_tokens_replay=1_200,
    estimated_time_replay_s=10.0,
)


# ── Registry ──

SURFACE_REGISTRY: Dict[str, SurfaceConfig] = {
    "kyb_aml": KYB_AML_CONFIG,
    "ehr_scheduling": EHR_SCHEDULING_CONFIG,
    "legacy_portal": LEGACY_PORTAL_CONFIG,
    "browser_checkout": BROWSER_CHECKOUT_CONFIG,
}


def get_surface_config(surface_id: str) -> Optional[SurfaceConfig]:
    """Get a surface configuration by ID."""
    return SURFACE_REGISTRY.get(surface_id)


def list_surfaces() -> List[Dict[str, Any]]:
    """List all available surfaces with metadata."""
    return [
        {
            "surface_id": cfg.surface_id,
            "name": cfg.name,
            "family": cfg.family,
            "surface_type": cfg.surface_type,
            "description": cfg.description,
            "steps": len(cfg.steps),
            "estimated_savings_pct": round(
                (1 - cfg.estimated_tokens_replay / max(cfg.estimated_tokens_full, 1)) * 100, 1
            ),
        }
        for cfg in SURFACE_REGISTRY.values()
    ]


def create_surface_trajectory(config: SurfaceConfig) -> TrajectoryLog:
    """
    Create a TrajectoryLog from a surface config.
    This produces a "template" trajectory that can be replayed.
    """
    steps = []
    for i, step_def in enumerate(config.steps):
        steps.append(TrajectoryStep(
            step_index=i,
            timestamp=datetime.now(timezone.utc).isoformat(),
            action=step_def["action"],
            state_before={},
            semantic_label=step_def.get("semantic_label"),
            mcp_tool_calls=[{
                "tool": step_def.get("tool", "unknown"),
                "params": step_def.get("params", {}),
            }] if step_def.get("tool") else None,
        ))

    return TrajectoryLog(
        trajectory_id=f"surface_{config.surface_id}_{uuid.uuid4().hex[:8]}",
        task_name=config.name,
        task_goal=config.description,
        device_id=f"surface_{config.surface_type}",
        started_at=datetime.now(timezone.utc).isoformat(),
        steps=steps,
        success=True,
        total_actions=len(steps),
        workflow_family=config.family,
        surface=config.surface_type,
        source_tokens_actual=config.estimated_tokens_full,
        source_time_actual_s=config.estimated_time_full_s,
    )


def get_surface_savings_comparison(surface_id: str) -> Optional[Dict[str, Any]]:
    """Get full vs replay savings comparison for a surface."""
    config = get_surface_config(surface_id)
    if not config:
        return None

    full = config.estimated_tokens_full
    replay = config.estimated_tokens_replay
    time_full = config.estimated_time_full_s
    time_replay = config.estimated_time_replay_s

    return {
        "surface_id": config.surface_id,
        "name": config.name,
        "full_run": {"tokens": full, "time_s": time_full, "cost_usd": round(full * 0.00000042, 4)},
        "replay": {"tokens": replay, "time_s": time_replay, "cost_usd": round(replay * 0.00000042, 4)},
        "savings": {
            "tokens_pct": round((1 - replay / max(full, 1)) * 100, 1),
            "time_pct": round((1 - time_replay / max(time_full, 0.1)) * 100, 1),
            "tokens_saved": full - replay,
            "time_saved_s": round(time_full - time_replay, 1),
        },
    }
