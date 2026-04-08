"""
QR Code Generation Service — monochrome QR codes for sharing.

Generates PNG QR codes for team invites, dashboards, benchmarks, ROPs, and demos.
Uses the monochrome sand theme (black on white, no colored gradients).
"""

from io import BytesIO
from typing import Optional

import qrcode
from qrcode.image.pil import PilImage

BASE_URL = "https://test-studio-xi.vercel.app"


def generate_qr(url: str, size: int = 10, border: int = 4) -> bytes:
    """Generate a monochrome QR code as PNG bytes.

    Args:
        url: The URL to encode
        size: Box size in pixels (default 10)
        border: Border width in boxes (default 4)
    """
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=size,
        border=border,
    )
    qr.add_data(url)
    qr.make(fit=True)
    img: PilImage = qr.make_image(fill_color="black", back_color="white")
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def team_invite_qr(invite_code: str) -> bytes:
    """QR for team onboarding — one-scan install + join."""
    return generate_qr(f"{BASE_URL}/join?team={invite_code}")


def dashboard_qr(team_code: str) -> bytes:
    """QR linking to team dashboard."""
    return generate_qr(f"{BASE_URL}/memory/team?team={team_code}")


def benchmark_qr(benchmark_id: str) -> bytes:
    """QR linking to a specific three-lane benchmark result."""
    return generate_qr(f"{BASE_URL}/benchmarks/three-lane?id={benchmark_id}")


def rop_qr(rop_id: str) -> bytes:
    """QR linking to a specific ROP detail page."""
    return generate_qr(f"{BASE_URL}/memory/rop?id={rop_id}")


def demo_qr() -> bytes:
    """QR linking to the main demo showcase."""
    return generate_qr(f"{BASE_URL}/demo")
