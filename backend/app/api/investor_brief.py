"""Investor brief control API router."""

from __future__ import annotations

from typing import Any, Literal, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..investor_brief import InvestorBriefService


router = APIRouter(prefix="/api/investor-brief", tags=["investor-brief"])

_investor_brief_service: Optional[InvestorBriefService] = None


class InvestorBriefActionRequest(BaseModel):
    action: Literal[
        "get_state",
        "list_sections",
        "get_section",
        "update_section",
        "set_scenario",
        "set_variables",
        "recalculate",
    ]
    arguments: dict[str, Any] = Field(default_factory=dict)


def set_investor_brief_service(service: Optional[InvestorBriefService]) -> None:
    global _investor_brief_service
    _investor_brief_service = service


def get_investor_brief_service() -> InvestorBriefService:
    if _investor_brief_service is None:
        raise HTTPException(status_code=503, detail="Investor brief service not initialized")
    return _investor_brief_service


@router.get("/state")
async def get_state() -> dict[str, Any]:
    return get_investor_brief_service().get_state()


@router.get("/sections")
async def list_sections() -> list[dict[str, Any]]:
    return get_investor_brief_service().list_sections()


@router.get("/sections/{section_id}")
async def get_section(section_id: str) -> dict[str, Any]:
    service = get_investor_brief_service()
    try:
        return service.get_section(section_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/actions")
async def apply_action(request: InvestorBriefActionRequest) -> dict[str, Any]:
    service = get_investor_brief_service()
    args = request.arguments
    try:
        if request.action == "get_state":
            result = service.get_state()
        elif request.action == "list_sections":
            result = service.list_sections()
        elif request.action == "get_section":
            result = service.get_section(args["section_id"])
        elif request.action == "update_section":
            result = service.update_section(
                section_id=args["section_id"],
                content=args["content"],
                content_format=args.get("content_format", "html"),
            )
        elif request.action == "set_scenario":
            result = service.set_scenario(args["scenario"])
        elif request.action == "set_variables":
            result = service.set_variables(args.get("variables", {}))
        else:
            result = service.recalculate()
        return {"action": request.action, "result": result}
    except KeyError as exc:
        raise HTTPException(status_code=400, detail=f"Missing required argument: {exc.args[0]}") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc