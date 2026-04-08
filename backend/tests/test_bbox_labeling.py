"""Unit tests for bbox label helpers used in screenshot annotation.

These helpers keep bounding-box labels readable (short, deduped) and try
to avoid label/label overlap when placing label pills.

Note: We intentionally test pure helpers only (no Mobile MCP / no PIL required).
"""

from pathlib import Path
import importlib.util

# NOTE: Importing via `app.agents.device_testing.tools.*` triggers a circular import
# in this repo (tools/__init__.py <-> coordinator imports). For unit tests, we load
# the module directly from its file path to keep these tests pure and focused.
_MODULE_PATH = Path(__file__).parent.parent / "app/agents/device_testing/tools/autonomous_navigation_tools.py"
_spec = importlib.util.spec_from_file_location("autonomous_navigation_tools", _MODULE_PATH)
assert _spec and _spec.loader
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)  # type: ignore[assignment]

_bbox_clean_label = _mod._bbox_clean_label
_bbox_ellipsize = _mod._bbox_ellipsize
_bbox_rects_overlap = _mod._bbox_rects_overlap
_bbox_find_label_position = _mod._bbox_find_label_position
_classify_element_type = _mod._classify_element_type
_is_interactive = _mod._is_interactive
_element_sort_key = _mod._element_sort_key
_ELEMENT_PALETTE = _mod._ELEMENT_PALETTE
_load_font = _mod._load_font


def test_bbox_clean_label_removes_bullets_and_dedupes_adjacent_words():
    assert _bbox_clean_label("Button • Button   Submit") == "Button Submit"
    assert _bbox_clean_label("  Hello   world  ") == "Hello world"


def test_bbox_ellipsize_handles_edge_cases():
    assert _bbox_ellipsize("abcdef", 0) == ""
    assert _bbox_ellipsize("abcdef", 1) == "…"
    assert _bbox_ellipsize("abc", 3) == "abc"
    assert _bbox_ellipsize("abcdef", 3) == "ab…"


def test_bbox_rects_overlap_basic():
    a = (0, 0, 10, 10)
    b = (20, 20, 30, 30)
    assert _bbox_rects_overlap(a, b, pad=0) is False

    c = (9, 9, 20, 20)
    assert _bbox_rects_overlap(a, c, pad=0) is True


def test_bbox_find_label_position_prefers_above_when_free():
    box = (50, 50, 100, 100)
    label_size = (40, 20)
    image_size = (200, 200)

    x, y, rect = _bbox_find_label_position(
        box=box,
        label_size=label_size,
        image_size=image_size,
        placed=[],
        margin=2,
        max_shifts=0,
    )

    # Above-left: y = 50 - 20 - 2 = 28
    assert (x, y) == (50, 28)
    assert rect == (50, 28, 90, 48)


def test_bbox_find_label_position_shifts_to_avoid_overlap():
    box = (50, 50, 100, 100)
    label_size = (40, 20)
    image_size = (200, 200)

    # Occupy the preferred above-left spot
    placed = [(50, 28, 90, 48)]

    x, y, rect = _bbox_find_label_position(
        box=box,
        label_size=label_size,
        image_size=image_size,
        placed=placed,
        margin=2,
        max_shifts=2,
    )

    # Next upward shift would land at y = 6 (28 - (20+2))
    assert (x, y) == (50, 6)
    assert rect == (50, 6, 90, 26)


# ---------------------------------------------------------------------------
# NEW: Element type classification tests (SoM-style)
# ---------------------------------------------------------------------------

def test_classify_element_type_android_classes():
    """Verify Android widget class names map to correct types."""
    assert _classify_element_type({"class": "android.widget.Button"}) == "button"
    assert _classify_element_type({"class": "android.widget.EditText"}) == "input"
    assert _classify_element_type({"class": "android.widget.Switch"}) == "toggle"
    assert _classify_element_type({"class": "android.widget.CheckBox"}) == "toggle"
    assert _classify_element_type({"class": "android.widget.ImageView"}) == "image"
    assert _classify_element_type({"class": "android.widget.TextView"}) == "text"
    assert _classify_element_type({"class": "android.widget.RadioButton"}) == "toggle"
    assert _classify_element_type({"class": "com.google.android.material.bottomnavigation.BottomNavigationView"}) == "nav"


def test_classify_element_type_clickable_fallback():
    """Clickable unknown elements classified as button."""
    assert _classify_element_type({"class": "com.custom.Widget", "clickable": True}) == "button"
    assert _classify_element_type({"class": "com.custom.Widget"}) == "unknown"


def test_is_interactive_checks_all_attrs():
    assert _is_interactive({"clickable": True}) is True
    assert _is_interactive({"focusable": True}) is True
    assert _is_interactive({"checkable": True}) is True
    assert _is_interactive({"editable": True}) is True
    assert _is_interactive({}) is False
    assert _is_interactive({"enabled": True}) is False  # enabled alone != interactive


def test_element_sort_key_interactive_first():
    """Interactive elements should sort before non-interactive."""
    btn = {"clickable": True, "coordinates": {"width": 50, "height": 30}}
    text = {"coordinates": {"width": 200, "height": 100}}
    sorted_keys = sorted([_element_sort_key(text), _element_sort_key(btn)])
    # Interactive (0, ...) comes before non-interactive (1, ...)
    assert sorted_keys[0][0] == 0
    assert sorted_keys[1][0] == 1
    # Verify area is computed correctly (nested MCP format)
    assert _element_sort_key(btn) == (0, -1500)
    assert _element_sort_key(text) == (1, -20000)


def test_element_sort_key_flat_adb_keys():
    """Sort key should work with flat ADB-style width/height keys."""
    adb_elem = {"clickable": True, "x": 10, "y": 20, "width": 300, "height": 80}
    key = _element_sort_key(adb_elem)
    assert key == (0, -24000), f"Expected (0, -24000), got {key}"


def test_element_palette_has_all_required_types():
    """Palette must have entries for all classification types."""
    required = {"button", "input", "toggle", "nav", "image", "text", "list", "container", "unknown"}
    assert set(_ELEMENT_PALETTE.keys()) >= required
    for etype, (border, fill, tag) in _ELEMENT_PALETTE.items():
        assert len(border) == 4, f"{etype} border must be RGBA"
        assert len(fill) == 4, f"{etype} fill must be RGBA"
        assert len(tag) > 0, f"{etype} must have a non-empty tag"


def test_load_font_returns_usable_font():
    """Font loading should always return something usable."""
    f = _load_font(20)
    assert f is not None
    # Verify it's a PIL font object
    assert hasattr(f, "getbbox") or hasattr(f, "getsize")

