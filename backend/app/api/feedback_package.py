"""Feedback Package API — structured QA reports for Claude Code consumption.

Assembles evidence from completed QA runs into a FeedbackPackage that
a user's Claude Code agent can parse to help debug their app.

Endpoints:
  POST /api/feedback/assemble           → build a feedback package from a QA run
  GET  /api/feedback/{package_id}       → retrieve a stored package
  GET  /api/feedback                    → list all packages
"""

import json
import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Literal, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..benchmarks.evidence_schema import BenchmarkRunEvidence, RunStatus
from ..benchmarks.evidence_writer import EvidenceWriter

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/feedback", tags=["feedback"])

PACKAGES_DIR = Path(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
) / "data" / "feedback_packages"
PACKAGES_DIR.mkdir(parents=True, exist_ok=True)


# ── Models ───────────────────────────────────────────────────────


class BugReport(BaseModel):
    bug_id: str
    title: str
    severity: Literal["critical", "major", "minor", "cosmetic"]
    description: str
    repro_steps: list[str]
    expected_behavior: str
    actual_behavior: str
    screenshot_paths: list[str] = Field(default_factory=list)
    action_span_score: Optional[float] = None
    confidence: float = 0.0


class FlowResult(BaseModel):
    flow_name: str
    status: Literal["pass", "fail", "blocked"]
    duration_seconds: float = 0.0
    bugs_found: list[BugReport] = Field(default_factory=list)
    action_span_clips: list[str] = Field(default_factory=list)


class FeedbackPackage(BaseModel):
    package_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    created_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    app_name: str = ""
    total_flows: int = 0
    passed: int = 0
    failed: int = 0
    blocked: int = 0
    total_bugs: int = 0
    critical_bugs: int = 0
    token_cost_usd: float = 0.0
    duration_seconds: float = 0.0
    flows: list[FlowResult] = Field(default_factory=list)
    improvement_suggestions: list[str] = Field(default_factory=list)
    report_url: Optional[str] = None

    def to_claude_code_summary(self) -> str:
        """Format as a concise markdown summary for Claude Code to consume."""
        lines: list[str] = []
        lines.append(f"## QA Report: {self.app_name}")
        lines.append(
            f"**{self.total_flows} flows tested** | "
            f"{self.passed} passed | {self.failed} failed | "
            f"{self.total_bugs} bugs found"
        )
        lines.append("")

        # Critical issues section
        critical = [
            bug
            for flow in self.flows
            for bug in flow.bugs_found
            if bug.severity == "critical"
        ]
        if critical:
            lines.append("### Critical Issues")
            for bug in critical:
                lines.append(
                    f"- [{bug.bug_id}] {bug.title} (confidence: {bug.confidence:.2f})"
                )
                if bug.repro_steps:
                    steps_str = " → ".join(bug.repro_steps)
                    lines.append(f"  - Steps: {steps_str}")
                if bug.description:
                    lines.append(f"  - Fix suggestion: {bug.description}")
            lines.append("")

        # Flow results table
        lines.append("### All Flow Results")
        lines.append("| Flow | Status | Duration | Bugs |")
        lines.append("|------|--------|----------|------|")
        for flow in self.flows:
            status_display = flow.status.upper()
            duration_display = f"{flow.duration_seconds:.1f}s"
            bug_count = len(flow.bugs_found)
            lines.append(
                f"| {flow.flow_name} | {status_display} | "
                f"{duration_display} | {bug_count} |"
            )
        lines.append("")

        # Cost summary
        minutes = int(self.duration_seconds // 60)
        seconds = int(self.duration_seconds % 60)
        lines.append("### Cost Summary")
        lines.append(f"- Token cost: ${self.token_cost_usd:.2f}")
        lines.append(f"- Total duration: {minutes}m {seconds}s")
        if self.report_url:
            lines.append(f"- Report: {self.report_url}")

        # Improvement suggestions
        if self.improvement_suggestions:
            lines.append("")
            lines.append("### Improvement Suggestions")
            for suggestion in self.improvement_suggestions:
                lines.append(f"- {suggestion}")

        return "\n".join(lines)


class AssembleRequest(BaseModel):
    suite_id: Optional[str] = None
    run_id: Optional[str] = None
    include_clips: bool = True


class PackageListItem(BaseModel):
    package_id: str
    app_name: str
    created_at: str
    total_bugs: int
    status_summary: str


# ── Helpers ──────────────────────────────────────────────────────


def _evidence_to_bug(
    ev: BenchmarkRunEvidence, index: int
) -> Optional[BugReport]:
    """Extract a BugReport from a failed evidence record."""
    if ev.status == RunStatus.PASS:
        return None

    severity: Literal["critical", "major", "minor", "cosmetic"] = "major"
    if ev.verdict.label and "deterministic" in ev.verdict.label.value:
        severity = "critical"
    elif ev.status == RunStatus.BLOCKED:
        severity = "critical"

    return BugReport(
        bug_id=f"BUG-{index:03d}",
        title=ev.verdict.reason or f"Failure in {ev.task_id}",
        severity=severity,
        description=ev.verdict.blocking_issue or ev.verdict.reason or "",
        repro_steps=[f"Execute task: {ev.task_id}"],
        expected_behavior="Task completes successfully",
        actual_behavior=ev.verdict.reason or f"Status: {ev.status.value}",
        screenshot_paths=ev.artifacts.screenshots,
        action_span_score=None,
        confidence=ev.verdict.confidence,
    )


def _evidence_to_flow(
    ev: BenchmarkRunEvidence, bug: Optional[BugReport]
) -> FlowResult:
    """Convert an evidence record into a FlowResult."""
    clips: list[str] = []
    if ev.artifacts.action_spans_path:
        clips.append(ev.artifacts.action_spans_path)

    return FlowResult(
        flow_name=ev.task_id,
        status=ev.status.value,  # type: ignore[arg-type]
        duration_seconds=ev.task_metrics.duration_seconds,
        bugs_found=[bug] if bug else [],
        action_span_clips=clips,
    )


def _build_package(
    evidences: list[BenchmarkRunEvidence],
    suite_id: str,
    include_clips: bool,
) -> FeedbackPackage:
    """Assemble a FeedbackPackage from a list of evidence records."""
    flows: list[FlowResult] = []
    all_bugs: list[BugReport] = []
    total_cost = 0.0
    total_duration = 0.0
    bug_counter = 1

    app_name = evidences[0].app_id if evidences else "unknown"

    for ev in evidences:
        bug = _evidence_to_bug(ev, bug_counter)
        if bug:
            all_bugs.append(bug)
            bug_counter += 1

        flow = _evidence_to_flow(ev, bug)
        if not include_clips:
            flow.action_span_clips = []
        flows.append(flow)

        total_cost += ev.cost.token_cost_usd
        total_duration += ev.task_metrics.duration_seconds

    passed = sum(1 for f in flows if f.status == "pass")
    failed = sum(1 for f in flows if f.status == "fail")
    blocked = sum(1 for f in flows if f.status == "blocked")
    critical = sum(1 for b in all_bugs if b.severity == "critical")

    # Generate improvement suggestions from failures
    suggestions: list[str] = []
    for bug in all_bugs:
        if bug.description:
            suggestions.append(f"Fix: {bug.title} — {bug.description}")

    return FeedbackPackage(
        app_name=app_name,
        total_flows=len(flows),
        passed=passed,
        failed=failed,
        blocked=blocked,
        total_bugs=len(all_bugs),
        critical_bugs=critical,
        token_cost_usd=round(total_cost, 4),
        duration_seconds=round(total_duration, 2),
        flows=flows,
        improvement_suggestions=suggestions,
        report_url=None,
    )


def _save_package(pkg: FeedbackPackage) -> Path:
    """Persist a FeedbackPackage to disk."""
    path = PACKAGES_DIR / f"{pkg.package_id}.json"
    path.write_text(pkg.model_dump_json(indent=2))
    return path


def _load_package(package_id: str) -> Optional[FeedbackPackage]:
    """Load a FeedbackPackage from disk."""
    path = PACKAGES_DIR / f"{package_id}.json"
    if not path.exists():
        return None
    return FeedbackPackage.model_validate_json(path.read_text())


# ── Routes ───────────────────────────────────────────────────────


@router.post("/assemble", response_model=FeedbackPackage, summary="Assemble a feedback package")
async def assemble_feedback(req: AssembleRequest) -> FeedbackPackage:
    """Build a FeedbackPackage from a completed QA run's evidence.

    Provide a suite_id to load all evidence for that suite, or a run_id
    to locate evidence by run ID. The assembled package is persisted and
    returned with a package_id for later retrieval.
    """
    writer = EvidenceWriter()

    if req.suite_id:
        evidences = writer.list_task_evidences(req.suite_id)
        if not evidences:
            raise HTTPException(
                status_code=404,
                detail=f"No evidence found for suite: {req.suite_id}",
            )
        pkg = _build_package(evidences, req.suite_id, req.include_clips)
    elif req.run_id:
        # Search across all suites for the matching run_id
        found: list[BenchmarkRunEvidence] = []
        for sid in writer.list_suites():
            for ev in writer.list_task_evidences(sid):
                if ev.run_id == req.run_id:
                    found.append(ev)
        if not found:
            raise HTTPException(
                status_code=404,
                detail=f"No evidence found for run: {req.run_id}",
            )
        pkg = _build_package(found, req.run_id, req.include_clips)
    else:
        raise HTTPException(
            status_code=400,
            detail="Provide either suite_id or run_id",
        )

    _save_package(pkg)
    logger.info(f"[FeedbackPackage] Assembled {pkg.package_id} — {pkg.total_bugs} bugs")
    return pkg


@router.get("/{package_id}", response_model=FeedbackPackage, summary="Retrieve a feedback package")
async def get_feedback(package_id: str) -> FeedbackPackage:
    """Retrieve a previously assembled feedback package by ID."""
    pkg = _load_package(package_id)
    if not pkg:
        raise HTTPException(status_code=404, detail=f"Package not found: {package_id}")
    return pkg


@router.get("", response_model=list[PackageListItem], summary="List all feedback packages")
async def list_feedback() -> list[PackageListItem]:
    """List all stored feedback packages with summary metadata."""
    items: list[PackageListItem] = []
    if not PACKAGES_DIR.exists():
        return items

    for f in sorted(PACKAGES_DIR.iterdir(), reverse=True):
        if not f.suffix == ".json":
            continue
        try:
            pkg = FeedbackPackage.model_validate_json(f.read_text())
            status = f"{pkg.passed}P/{pkg.failed}F/{pkg.blocked}B"
            items.append(
                PackageListItem(
                    package_id=pkg.package_id,
                    app_name=pkg.app_name,
                    created_at=pkg.created_at,
                    total_bugs=pkg.total_bugs,
                    status_summary=status,
                )
            )
        except Exception as exc:
            logger.warning(f"Skipping malformed package {f.name}: {exc}")
    return items
