"""Shareable Reports API.

Creates short-URL shareable reports from benchmark suites and pipeline runs.
Reports are accessible at /r/{report_id} as self-contained HTML pages.

Endpoints:
  POST /api/reports            - Create a shareable report
  GET  /api/reports            - List all reports
  GET  /r/{report_id}          - Serve HTML report page
  DELETE /api/reports/{report_id} - Delete a report
"""

import json
import logging
import os
import secrets
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request  # noqa: F401
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from ..benchmarks.evidence_writer import EvidenceWriter

logger = logging.getLogger(__name__)

router = APIRouter(tags=["reports"])

DATA_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
    "data",
)
REPORTS_JSON = os.path.join(DATA_DIR, "reports.json")
PIPELINE_RESULTS_DIR = os.path.join(DATA_DIR, "pipeline_results")


# ── Models ────────────────────────────────────────────────────

class CreateReportRequest(BaseModel):
    suite_id: Optional[str] = Field(default=None, description="Benchmark suite ID")
    run_id: Optional[str] = Field(default=None, description="Pipeline run ID")
    title: Optional[str] = Field(default=None, description="Report title override")


class ReportEntry(BaseModel):
    report_id: str
    title: str
    created_at: str
    suite_id: Optional[str] = None
    run_id: Optional[str] = None


class CreateReportResponse(BaseModel):
    report_id: str
    url: str


# ── Persistence helpers ───────────────────────────────────────

def _load_reports() -> Dict[str, Any]:
    if not os.path.exists(REPORTS_JSON):
        return {}
    try:
        with open(REPORTS_JSON, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return {}


def _save_reports(reports: Dict[str, Any]) -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(REPORTS_JSON, "w") as f:
        json.dump(reports, f, indent=2, default=str)


def _load_pipeline_result(run_id: str) -> Optional[Dict]:
    """Load a pipeline result from persisted JSON files."""
    if not os.path.isdir(PIPELINE_RESULTS_DIR):
        return None
    for fname in os.listdir(PIPELINE_RESULTS_DIR):
        if fname.endswith(".json") and run_id in fname:
            path = os.path.join(PIPELINE_RESULTS_DIR, fname)
            try:
                with open(path, "r") as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError):
                continue
    return None


# ── HTML rendering ────────────────────────────────────────────

def _esc(text: str) -> str:
    """Escape text for safe HTML embedding."""
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#x27;")
    )


def _verdict_color(verdict: str) -> str:
    v = verdict.lower()
    if v in ("pass", "success"):
        return "#4caf50"
    if v in ("fail", "bug-found", "bug-found-deterministic", "wrong-output"):
        return "#ff6b6b"
    if v in ("blocked", "timeout", "infra-failure"):
        return "#ffa94d"
    return "#888"


def _render_suite_report(report: Dict, writer: EvidenceWriter) -> str:
    """Render a benchmark suite as a self-contained HTML report."""
    suite_id = report["suite_id"]
    title = _esc(report.get("title", f"Suite {suite_id}"))
    created = _esc(report.get("created_at", ""))

    manifest = writer.load_suite_manifest(suite_id)
    scorecard = writer.load_scorecard(suite_id)
    evidences = writer.list_task_evidences(suite_id)

    total = len(evidences)
    passed = sum(1 for e in evidences if e.status.value == "pass")
    failed = sum(1 for e in evidences if e.status.value == "fail")
    blocked = total - passed - failed
    total_duration = sum(e.task_metrics.duration_seconds for e in evidences)
    total_cost = sum(e.cost.total_cost_usd for e in evidences)
    total_input_tokens = sum(e.cost.token_input for e in evidences)
    total_output_tokens = sum(e.cost.token_output for e in evidences)

    # Build test cards
    test_cards_html = ""
    for ev in evidences:
        color = _verdict_color(ev.status.value)
        screenshots_html = ""
        for ss in ev.artifacts.screenshots[:3]:
            screenshots_html += (
                f'<span style="color:#555;font-size:11px;margin-right:8px;">'
                f'📷 {_esc(os.path.basename(ss))}</span>'
            )

        test_cards_html += f"""
        <div style="background:#141414;border:1px solid #222;border-radius:8px;padding:16px;margin-bottom:8px;">
          <div style="display:flex;align-items:center;gap:8px;margin-bottom:8px;">
            <span style="display:inline-block;width:10px;height:10px;border-radius:50%;background:{color};"></span>
            <span style="font-family:monospace;font-size:13px;font-weight:600;">{_esc(ev.task_id)}</span>
            <span style="color:{color};font-size:12px;font-weight:600;text-transform:uppercase;">{_esc(ev.status.value)}</span>
            <span style="color:#555;font-size:11px;margin-left:auto;">{ev.task_metrics.duration_seconds:.1f}s</span>
          </div>
          <div style="font-size:13px;color:#aaa;margin-bottom:4px;">
            <strong>Verdict:</strong> {_esc(ev.verdict.label.value)}
            (confidence: {ev.verdict.confidence:.0%})
          </div>
          <div style="font-size:12px;color:#888;">{_esc(ev.verdict.reason)}</div>
          <div style="margin-top:6px;">{screenshots_html}</div>
          <div style="font-size:11px;color:#555;margin-top:6px;">
            Tokens: {ev.cost.token_input:,} in / {ev.cost.token_output:,} out
            &middot; Cost: ${ev.cost.total_cost_usd:.4f}
            &middot; Artifacts: {ev.artifacts.completeness_score():.0%}
          </div>
        </div>"""

    # Scorecard section
    scorecard_html = ""
    if scorecard:
        scorecard_html = '<div style="margin-bottom:24px;">'
        scorecard_html += '<h2 style="font-size:16px;font-weight:600;color:#ccc;margin-bottom:12px;">Scorecard</h2>'
        scorecard_html += '<div style="background:#141414;border:1px solid #222;border-radius:8px;padding:16px;font-family:monospace;font-size:12px;color:#aaa;white-space:pre-wrap;">'
        scorecard_html += _esc(json.dumps(scorecard, indent=2, default=str))
        scorecard_html += "</div></div>"

    return _report_shell(
        title=title,
        badge="Benchmark Suite",
        created=created,
        meta_cards=f"""
        <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:16px;margin-bottom:24px;">
          {_meta_card("Total Tests", str(total))}
          {_meta_card("Passed", str(passed), "#4caf50")}
          {_meta_card("Failed", str(failed), "#ff6b6b" if failed else "#888")}
          {_meta_card("Blocked", str(blocked), "#ffa94d" if blocked else "#888")}
          {_meta_card("Duration", f"{total_duration:.1f}s")}
          {_meta_card("Total Cost", f"${total_cost:.4f}")}
          {_meta_card("Input Tokens", f"{total_input_tokens:,}")}
          {_meta_card("Output Tokens", f"{total_output_tokens:,}")}
        </div>""",
        body=f"""
        {scorecard_html}
        <h2 style="font-size:16px;font-weight:600;color:#ccc;margin-bottom:12px;">Test Results</h2>
        {test_cards_html}
        <div style="margin-top:32px;">
          <h2 style="font-size:16px;font-weight:600;color:#ccc;margin-bottom:12px;">Token Cost Breakdown</h2>
          <div style="background:#141414;border:1px solid #222;border-radius:8px;padding:16px;">
            <table style="width:100%;font-size:13px;border-collapse:collapse;">
              <tr style="color:#888;text-align:left;">
                <th style="padding:6px 8px;">Task</th>
                <th style="padding:6px 8px;">Input</th>
                <th style="padding:6px 8px;">Output</th>
                <th style="padding:6px 8px;">Token Cost</th>
                <th style="padding:6px 8px;">Total Cost</th>
              </tr>
              {"".join(
                  f'<tr style="border-top:1px solid #1a1a1a;">'
                  f'<td style="padding:6px 8px;font-family:monospace;">{_esc(e.task_id)}</td>'
                  f'<td style="padding:6px 8px;">{e.cost.token_input:,}</td>'
                  f'<td style="padding:6px 8px;">{e.cost.token_output:,}</td>'
                  f'<td style="padding:6px 8px;">${e.cost.token_cost_usd:.4f}</td>'
                  f'<td style="padding:6px 8px;">${e.cost.total_cost_usd:.4f}</td>'
                  f'</tr>'
                  for e in evidences
              )}
              <tr style="border-top:2px solid #333;font-weight:700;">
                <td style="padding:6px 8px;">TOTAL</td>
                <td style="padding:6px 8px;">{total_input_tokens:,}</td>
                <td style="padding:6px 8px;">{total_output_tokens:,}</td>
                <td style="padding:6px 8px;">${sum(e.cost.token_cost_usd for e in evidences):.4f}</td>
                <td style="padding:6px 8px;">${total_cost:.4f}</td>
              </tr>
            </table>
          </div>
        </div>""",
    )


def _render_pipeline_report(report: Dict) -> str:
    """Render a pipeline run result as a self-contained HTML report."""
    run_id = report.get("run_id", "")
    title = _esc(report.get("title", f"Pipeline {run_id}"))
    created = _esc(report.get("created_at", ""))

    result = _load_pipeline_result(run_id)
    if not result:
        return _report_shell(
            title=title,
            badge="Pipeline Run",
            created=created,
            meta_cards="",
            body='<div style="text-align:center;padding:60px;color:#666;">Pipeline result data not found.</div>',
        )

    # Extract common pipeline fields
    app_name = _esc(result.get("app_name", result.get("app_id", run_id)))
    status = result.get("status", "unknown")
    tests = result.get("tests", result.get("test_cases", []))
    total = len(tests)
    duration = result.get("duration_seconds", result.get("duration", 0))
    cost_data = result.get("cost", {})
    total_cost = cost_data.get("total_cost_usd", 0) if isinstance(cost_data, dict) else 0

    # Build test cards
    test_cards_html = ""
    for t in tests:
        t_name = t.get("name", t.get("test_id", "unnamed"))
        t_status = t.get("status", t.get("verdict", "unknown"))
        t_color = _verdict_color(str(t_status))
        t_desc = t.get("description", t.get("reason", ""))
        t_steps = t.get("steps", [])

        steps_html = ""
        for i, step in enumerate(t_steps[:10], 1):
            step_text = step if isinstance(step, str) else step.get("action", step.get("description", str(step)))
            steps_html += f'<div style="display:flex;gap:8px;padding:4px 0;border-top:1px solid #1a1a1a;font-size:12px;"><span style="color:#555;min-width:20px;">{i}.</span><span>{_esc(str(step_text))}</span></div>'

        test_cards_html += f"""
        <div style="background:#141414;border:1px solid #222;border-radius:8px;padding:16px;margin-bottom:8px;">
          <div style="display:flex;align-items:center;gap:8px;margin-bottom:6px;">
            <span style="display:inline-block;width:10px;height:10px;border-radius:50%;background:{t_color};"></span>
            <span style="font-family:monospace;font-size:13px;font-weight:600;">{_esc(str(t_name))}</span>
            <span style="color:{t_color};font-size:12px;font-weight:600;text-transform:uppercase;">{_esc(str(t_status))}</span>
          </div>
          <div style="font-size:12px;color:#888;">{_esc(str(t_desc))}</div>
          {f'<div style="margin-top:8px;">{steps_html}</div>' if steps_html else ""}
        </div>"""

    return _report_shell(
        title=title,
        badge="Pipeline Run",
        created=created,
        meta_cards=f"""
        <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:16px;margin-bottom:24px;">
          {_meta_card("App", app_name)}
          {_meta_card("Status", status.upper(), _verdict_color(status))}
          {_meta_card("Tests", str(total))}
          {_meta_card("Duration", f"{duration:.1f}s" if isinstance(duration, (int, float)) else str(duration))}
          {_meta_card("Total Cost", f"${total_cost:.4f}" if isinstance(total_cost, (int, float)) else str(total_cost))}
        </div>""",
        body=f"""
        <h2 style="font-size:16px;font-weight:600;color:#ccc;margin-bottom:12px;">Test Results</h2>
        {test_cards_html if test_cards_html else '<div style="text-align:center;padding:40px;color:#666;">No test data available.</div>'}
        """,
    )


def _meta_card(label: str, value: str, color: str = "#fff") -> str:
    return (
        f'<div style="background:#141414;border:1px solid #222;border-radius:8px;padding:16px;">'
        f'<div style="color:#888;font-size:12px;text-transform:uppercase;letter-spacing:0.5px;">{_esc(label)}</div>'
        f'<div style="font-size:24px;font-weight:700;margin-top:4px;color:{color};">{_esc(value)}</div>'
        f'</div>'
    )


def _report_shell(title: str, badge: str, created: str, meta_cards: str, body: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>retention.sh — {title}</title>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{ font-family: 'SF Mono', 'Fira Code', 'Cascadia Code', monospace; background: #0a0a0a; color: #e0e0e0; }}
  a {{ color: #7c8aff; text-decoration: none; }}
  a:hover {{ text-decoration: underline; }}
</style>
</head>
<body>
<div style="background:#111;border-bottom:1px solid #222;padding:16px 24px;display:flex;align-items:center;gap:16px;">
  <h1 style="font-size:18px;font-weight:600;color:#fff;">retention.sh</h1>
  <span style="background:#1a1a2e;color:#7c8aff;padding:4px 10px;border-radius:12px;font-size:12px;">{_esc(badge)}</span>
</div>
<div style="max-width:1200px;margin:0 auto;padding:24px;">
  <h2 style="font-size:22px;font-weight:700;color:#fff;margin-bottom:4px;">{title}</h2>
  <div style="font-size:12px;color:#555;margin-bottom:24px;">{created}</div>
  {meta_cards}
  {body}
  <div style="margin-top:48px;padding-top:16px;border-top:1px solid #222;font-size:11px;color:#444;text-align:center;">
    Generated by retention.sh &middot; retention.sh
  </div>
</div>
</body>
</html>"""


# ── Endpoints ─────────────────────────────────────────────────

def _resolve_caller(request: Request) -> str:
    """Extract caller email from Bearer token, or 'anonymous'."""
    auth = request.headers.get("authorization", "")
    if auth.startswith("Bearer "):
        try:
            from .signup import _load_keys, _lookup_by_token
            _, info = _lookup_by_token(_load_keys(), auth[7:].strip())
            if info:
                return info.get("email", "anonymous")
        except Exception:
            pass
    return "anonymous"


@router.post("/api/reports", response_model=CreateReportResponse, summary="Create a shareable report")
async def create_report(req: CreateReportRequest, request: Request) -> CreateReportResponse:
    """Generate a short-URL shareable report from a benchmark suite or pipeline run."""
    caller = _resolve_caller(request)
    if not req.suite_id and not req.run_id:
        raise HTTPException(status_code=400, detail="Provide suite_id or run_id")

    report_id = secrets.token_hex(4)  # 8-char hex

    # Determine title
    title = req.title
    if not title:
        if req.suite_id:
            title = f"Benchmark: {req.suite_id}"
        else:
            title = f"Pipeline: {req.run_id}"

    entry = {
        "report_id": report_id,
        "title": title,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "created_by": caller,
        "suite_id": req.suite_id,
        "run_id": req.run_id,
    }

    reports = _load_reports()
    reports[report_id] = entry
    _save_reports(reports)

    logger.info(f"[Reports] Created report {report_id} for suite={req.suite_id} run={req.run_id}")
    return CreateReportResponse(report_id=report_id, url=f"/r/{report_id}")


@router.get("/api/reports", response_model=List[ReportEntry], summary="List all shareable reports")
async def list_reports(request: Request) -> List[ReportEntry]:
    """Return shareable reports filtered by caller ownership (newest first)."""
    caller = _resolve_caller(request)
    reports = _load_reports()
    entries = []
    for v in reports.values():
        owner = v.get("created_by", "anonymous")
        if caller != "anonymous" and owner != "anonymous" and owner != caller:
            continue
        entries.append(ReportEntry(**v))
    entries.sort(key=lambda e: e.created_at, reverse=True)
    return entries


@router.delete("/api/reports/{report_id}", summary="Delete a report")
async def delete_report(report_id: str, request: Request) -> Dict[str, str]:
    """Remove a shareable report. Only the creator can delete."""
    caller = _resolve_caller(request)
    reports = _load_reports()
    if report_id not in reports:
        raise HTTPException(status_code=404, detail=f"Report not found: {report_id}")
    creator = reports[report_id].get("created_by", "anonymous")
    if creator != "anonymous" and caller != "anonymous" and creator != caller:
        raise HTTPException(status_code=403, detail="Only the report creator can delete it")
    del reports[report_id]
    _save_reports(reports)
    logger.info(f"[Reports] Deleted report {report_id} by {caller}")
    return {"status": "deleted", "report_id": report_id}


@router.get("/r/{report_id}", response_class=HTMLResponse, summary="View shareable report")
async def view_report(report_id: str) -> HTMLResponse:
    """Serve a self-contained HTML report page."""
    reports = _load_reports()
    report = reports.get(report_id)
    if not report:
        raise HTTPException(status_code=404, detail=f"Report not found: {report_id}")

    writer = EvidenceWriter()

    if report.get("suite_id"):
        html = _render_suite_report(report, writer)
    elif report.get("run_id"):
        html = _render_pipeline_report(report)
    else:
        raise HTTPException(status_code=400, detail="Report has no suite_id or run_id")

    return HTMLResponse(content=html)
