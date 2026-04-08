"""
QR Code API routes — serve QR code PNGs for sharing.
"""

from fastapi import APIRouter, Response

from ..services.qr_service import (
    benchmark_qr,
    dashboard_qr,
    demo_qr,
    rop_qr,
    team_invite_qr,
)

router = APIRouter(prefix="/api/qr", tags=["qr-codes"])


@router.get("/team-invite/{invite_code}")
async def get_team_invite_qr(invite_code: str):
    """QR code for team onboarding."""
    return Response(content=team_invite_qr(invite_code), media_type="image/png")


@router.get("/dashboard/{team_code}")
async def get_dashboard_qr(team_code: str):
    """QR code linking to team dashboard."""
    return Response(content=dashboard_qr(team_code), media_type="image/png")


@router.get("/benchmark/{benchmark_id}")
async def get_benchmark_qr(benchmark_id: str):
    """QR code linking to a three-lane benchmark result."""
    return Response(content=benchmark_qr(benchmark_id), media_type="image/png")


@router.get("/rop/{rop_id}")
async def get_rop_qr(rop_id: str):
    """QR code linking to a ROP detail page."""
    return Response(content=rop_qr(rop_id), media_type="image/png")


@router.get("/demo")
async def get_demo_qr():
    """QR code linking to the demo showcase."""
    return Response(content=demo_qr(), media_type="image/png")
