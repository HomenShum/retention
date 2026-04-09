"""
Benchmark App Generation — Controllable Expo React Native apps for QA evaluation.

Generates benchmark target apps with:
- Realistic mobile workflows (booking, profile, feed, settings)
- Planted bugs at configurable difficulty levels
- Change request backlog for before/after evaluation
- Android APK build via Expo/EAS
- Structured scenario registry for reproducible benchmarking

Architecture:
  generate_benchmark_app(template, bug_profile)
    → Expo React Native source
    → planted bugs injected
    → eas build --platform android --profile preview --local
    → APK artifact
    → registered in benchmark_apps/app_registry.json
"""

import asyncio
import json
import logging
import os
import shutil
import subprocess
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data directory
# ---------------------------------------------------------------------------

_DATA_DIR = Path(__file__).resolve().parents[2] / "data" / "benchmark_apps"
_DATA_DIR.mkdir(parents=True, exist_ok=True)

_GENERATED_DIR = Path(__file__).resolve().parents[2] / "data" / "benchmark_generated"
_GENERATED_DIR.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Templates — starter app definitions
# ---------------------------------------------------------------------------

BENCHMARK_TEMPLATES: Dict[str, dict] = {
    "booking": {
        "name": "BookIt — Appointment Scheduler",
        "description": "Booking/scheduling app with calendar, time slots, confirmation flow",
        "screens": ["home", "calendar", "time_picker", "booking_form", "confirmation", "my_bookings"],
        "workflows": ["browse_available_slots", "book_appointment", "cancel_booking", "reschedule", "view_history"],
        "expo_template": "tabs",
    },
    "profile": {
        "name": "ProfileHub — User Onboarding",
        "description": "Onboarding/profile app with signup, avatar, settings, preferences",
        "screens": ["welcome", "signup", "profile_setup", "avatar_picker", "preferences", "settings"],
        "workflows": ["complete_signup", "edit_profile", "change_avatar", "update_preferences", "toggle_notifications"],
        "expo_template": "tabs",
    },
    "feed": {
        "name": "FeedFlow — Social Content Feed",
        "description": "Social feed with posts, comments, likes, share, user profiles",
        "screens": ["feed", "post_detail", "comments", "user_profile", "create_post", "notifications"],
        "workflows": ["scroll_feed", "view_post", "add_comment", "like_post", "create_post", "view_profile"],
        "expo_template": "tabs",
    },
    "ecommerce": {
        "name": "QuickCart — Mobile Shop",
        "description": "E-commerce app with product list, detail, cart, checkout",
        "screens": ["product_list", "product_detail", "cart", "checkout", "order_confirmation", "search"],
        "workflows": ["browse_products", "add_to_cart", "checkout_flow", "search_product", "view_order"],
        "expo_template": "tabs",
    },
    "settings": {
        "name": "ConfigPanel — App Settings",
        "description": "Settings app with toggles, dropdowns, forms, navigation hierarchy",
        "screens": ["settings_main", "account", "notifications", "appearance", "privacy", "about"],
        "workflows": ["toggle_dark_mode", "change_notification_prefs", "update_account_info", "clear_cache", "logout"],
        "expo_template": "tabs",
    },
}


# ---------------------------------------------------------------------------
# Bug profiles — planted bugs at different difficulty levels
# ---------------------------------------------------------------------------

BUG_PROFILES: Dict[str, List[dict]] = {
    "easy": [
        {"type": "missing_handler", "description": "Button has no onPress handler", "severity": "high", "detection": "functional"},
        {"type": "wrong_text", "description": "Label shows wrong text content", "severity": "medium", "detection": "visual"},
        {"type": "invisible_element", "description": "Element has opacity: 0 or display: none", "severity": "high", "detection": "visual"},
    ],
    "medium": [
        {"type": "wrong_navigation", "description": "Button navigates to wrong screen", "severity": "high", "detection": "functional"},
        {"type": "form_validation_missing", "description": "Form submits without validating required fields", "severity": "high", "detection": "functional"},
        {"type": "stale_data", "description": "List doesn't refresh after add/delete", "severity": "medium", "detection": "functional"},
        {"type": "off_by_one", "description": "Counter shows wrong count (off by 1)", "severity": "medium", "detection": "data"},
    ],
    "hard": [
        {"type": "race_condition", "description": "Double-tap creates duplicate entries", "severity": "high", "detection": "timing"},
        {"type": "state_leak", "description": "State from previous screen bleeds into next", "severity": "high", "detection": "navigation"},
        {"type": "async_error_swallowed", "description": "API error silently fails, shows stale success state", "severity": "critical", "detection": "error_handling"},
        {"type": "memory_leak_scroll", "description": "Infinite scroll doesn't clean up unmounted components", "severity": "medium", "detection": "performance"},
    ],
}


# ---------------------------------------------------------------------------
# Benchmark case — a single benchmark run definition
# ---------------------------------------------------------------------------

@dataclass
class BenchmarkCase:
    """A single benchmark case: app + bugs + change requests."""
    case_id: str
    template: str
    app_name: str
    planted_bugs: List[dict] = field(default_factory=list)
    change_requests: List[dict] = field(default_factory=list)
    acceptance_criteria: List[str] = field(default_factory=list)
    difficulty: str = "medium"
    created_at: str = ""
    apk_path: Optional[str] = None
    source_path: Optional[str] = None
    status: str = "pending"  # pending, generating, building, ready, failed

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# Benchmark run — tracks a full benchmark execution
# ---------------------------------------------------------------------------

@dataclass
class BenchmarkRun:
    """Tracks a complete benchmark run: generate → build → install → QA → fix → rerun."""
    run_id: str
    case_id: str
    started_at: str
    status: str = "running"  # running, complete, failed
    phases: Dict[str, dict] = field(default_factory=dict)
    qa_run_ids: List[str] = field(default_factory=list)
    total_time_s: float = 0.0
    total_tokens: int = 0
    bugs_found: int = 0
    bugs_planted: int = 0
    precision: float = 0.0
    recall: float = 0.0
    f1: float = 0.0
    thread_mode: str = "fresh"  # fresh or continuous
    completed_at: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# Registry — persisted benchmark cases
# ---------------------------------------------------------------------------

_CASES_FILE = _DATA_DIR / "benchmark_cases.json"
_RUNS_FILE = _DATA_DIR / "benchmark_run_history.json"


def _load_cases() -> List[dict]:
    if _CASES_FILE.exists():
        try:
            return json.loads(_CASES_FILE.read_text())
        except Exception:
            pass
    return []


def _save_cases(cases: List[dict]) -> None:
    _CASES_FILE.write_text(json.dumps(cases, indent=2, default=str))


def _load_runs() -> List[dict]:
    if _RUNS_FILE.exists():
        try:
            return json.loads(_RUNS_FILE.read_text())
        except Exception:
            pass
    return []


def _save_run(run: dict) -> None:
    runs = _load_runs()
    # Update existing or append
    existing = next((i for i, r in enumerate(runs) if r.get("run_id") == run["run_id"]), None)
    if existing is not None:
        runs[existing] = run
    else:
        runs.append(run)
    _RUNS_FILE.write_text(json.dumps(runs, indent=2, default=str))


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# App generation — creates Expo source with planted bugs
# ---------------------------------------------------------------------------

async def generate_benchmark_app(
    template: str = "ecommerce",
    difficulty: str = "medium",
    num_bugs: int = 5,
    change_requests: Optional[List[str]] = None,
) -> BenchmarkCase:
    """Generate a benchmark Expo React Native app with planted bugs.

    Args:
        template: App template from BENCHMARK_TEMPLATES
        difficulty: Bug difficulty level (easy, medium, hard, mixed)
        num_bugs: Number of bugs to plant
        change_requests: Optional list of feature change requests for before/after eval

    Returns:
        BenchmarkCase with source_path and planted bug manifest
    """
    if template not in BENCHMARK_TEMPLATES:
        raise ValueError(f"Unknown template: {template}. Available: {list(BENCHMARK_TEMPLATES.keys())}")

    tmpl = BENCHMARK_TEMPLATES[template]
    case_id = f"bench-{template}-{uuid.uuid4().hex[:8]}"
    case_dir = _GENERATED_DIR / case_id
    case_dir.mkdir(parents=True, exist_ok=True)

    # Select bugs to plant
    if difficulty == "mixed":
        bug_pool = BUG_PROFILES["easy"] + BUG_PROFILES["medium"] + BUG_PROFILES["hard"]
    else:
        bug_pool = BUG_PROFILES.get(difficulty, BUG_PROFILES["medium"])

    import random
    planted = []
    for i in range(min(num_bugs, len(bug_pool))):
        bug = bug_pool[i % len(bug_pool)].copy()
        bug["bug_id"] = f"PLANTED-{i+1:03d}"
        bug["screen"] = random.choice(tmpl["screens"])
        planted.append(bug)

    # Generate app source using LLM
    app_prompt = _build_generation_prompt(tmpl, planted, change_requests)

    # Use OpenAI to generate the Expo app source
    try:
        import openai
        client = openai.AsyncOpenAI()

        response = await client.chat.completions.create(
            model="gpt-5.4-mini",
            messages=[
                {"role": "system", "content": _EXPO_SYSTEM_PROMPT},
                {"role": "user", "content": app_prompt},
            ],
            max_completion_tokens=16000,
            temperature=0.3,
        )

        source_code = response.choices[0].message.content or ""

        # Parse the generated source into files
        files = _parse_generated_source(source_code)

        # Write files to case directory
        for fname, content in files.items():
            fpath = case_dir / fname
            fpath.parent.mkdir(parents=True, exist_ok=True)
            fpath.write_text(content, encoding="utf-8")

        # Write bug manifest
        manifest = {
            "app_name": tmpl["name"],
            "case_id": case_id,
            "template": template,
            "difficulty": difficulty,
            "total_planted_bugs": len(planted),
            "bugs": planted,
            "change_requests": change_requests or [],
            "acceptance_criteria": [
                f"All {len(tmpl['screens'])} screens render without crashes",
                "Navigation between screens works",
                f"All {len(planted)} planted bugs are detectable by QA agent",
            ],
            "screens": tmpl["screens"],
            "workflows": tmpl["workflows"],
            "generated_at": _now_iso(),
        }
        (case_dir / "bug_manifest.json").write_text(json.dumps(manifest, indent=2))

        # Write package.json for Expo
        pkg = {
            "name": case_id,
            "version": "1.0.0",
            "main": "expo-router/entry",
            "scripts": {
                "start": "expo start",
                "android": "expo start --android",
                "build:android": "eas build --platform android --profile preview --local",
            },
            "dependencies": {
                "expo": "~52.0.0",
                "expo-router": "~4.0.0",
                "react": "18.3.1",
                "react-dom": "18.3.1",
                "react-native": "0.76.0",
                "react-native-web": "~0.19.13",
                "@expo/vector-icons": "^14.0.0",
                "@expo/metro-runtime": "~4.0.0",
                "react-native-safe-area-context": "4.12.0",
                "react-native-screens": "~4.1.0",
                "react-native-gesture-handler": "~2.20.0",
                "react-native-reanimated": "~3.16.0",
                "expo-linking": "~7.0.0",
                "expo-constants": "~17.0.0",
                "expo-status-bar": "~2.0.0",
            },
        }
        (case_dir / "package.json").write_text(json.dumps(pkg, indent=2))

        # Write app.json (required by Expo)
        app_json = {
            "expo": {
                "name": tmpl["name"],
                "slug": case_id,
                "version": "1.0.0",
                "scheme": case_id,
                "platforms": ["android", "web"],
                "android": {
                    "package": f"com.benchmark.{case_id.replace('-', '_')}",
                },
                "web": {
                    "bundler": "metro",
                    "output": "single",
                },
                "plugins": ["expo-router"],
            }
        }
        (case_dir / "app.json").write_text(json.dumps(app_json, indent=2))

    except Exception as e:
        logger.error(f"Failed to generate benchmark app: {e}")
        # Fallback: create a minimal HTML benchmark app (web-based)
        html = _generate_html_benchmark(tmpl, planted)
        (case_dir / "index.html").write_text(html, encoding="utf-8")
        manifest = {
            "app_name": tmpl["name"],
            "case_id": case_id,
            "template": template,
            "difficulty": difficulty,
            "total_planted_bugs": len(planted),
            "bugs": planted,
            "type": "html_fallback",
            "generated_at": _now_iso(),
        }
        (case_dir / "bug_manifest.json").write_text(json.dumps(manifest, indent=2))

    case = BenchmarkCase(
        case_id=case_id,
        template=template,
        app_name=tmpl["name"],
        planted_bugs=planted,
        change_requests=change_requests or [],
        acceptance_criteria=manifest.get("acceptance_criteria", []),
        difficulty=difficulty,
        created_at=_now_iso(),
        source_path=str(case_dir),
        status="generated",
    )

    # Persist case
    cases = _load_cases()
    cases.append(case.to_dict())
    _save_cases(cases)

    logger.info(f"Generated benchmark app: {case_id} ({template}, {len(planted)} bugs)")
    return case


# ---------------------------------------------------------------------------
# Build orchestration — Expo → Android APK or Web bundle
# ---------------------------------------------------------------------------

_ANDROID_HOME = os.environ.get("ANDROID_HOME", os.path.expanduser("~/Library/Android/sdk"))
_JAVA_HOME = os.environ.get("JAVA_HOME", "/opt/homebrew/opt/openjdk@17/libexec/openjdk.jdk/Contents/Home")


def _build_env() -> dict:
    """Environment for subprocess builds — ensures Java + Android SDK are on PATH."""
    env = os.environ.copy()
    env["ANDROID_HOME"] = _ANDROID_HOME
    env["ANDROID_SDK_ROOT"] = _ANDROID_HOME
    if os.path.exists(_JAVA_HOME):
        env["JAVA_HOME"] = _JAVA_HOME
        env["PATH"] = f"{_JAVA_HOME}/bin:{env.get('PATH', '')}"
    env["PATH"] = f"{_ANDROID_HOME}/platform-tools:{_ANDROID_HOME}/emulator:{env['PATH']}"
    return env


async def _run_subprocess(cmd: List[str], cwd: str, timeout: int = 300, label: str = "") -> tuple:
    """Run a subprocess with timeout, return (success, stdout, stderr)."""
    logger.info(f"[build] {label or ' '.join(cmd[:3])}...")
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=_build_env(),
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        success = proc.returncode == 0
        if not success:
            logger.warning(f"[build] {label} failed (rc={proc.returncode}): {stderr.decode()[:300]}")
        return success, stdout.decode(), stderr.decode()
    except asyncio.TimeoutError:
        logger.error(f"[build] {label} timed out after {timeout}s")
        return False, "", f"Timed out after {timeout}s"
    except Exception as e:
        logger.error(f"[build] {label} error: {e}")
        return False, "", str(e)


async def build_android_apk(case_id: str) -> dict:
    """Build an Android APK from a generated benchmark app.

    Tries in order:
    1. Expo prebuild + gradle assembleDebug (real APK)
    2. Expo web export (web bundle served in Chrome on emulator)
    3. HTML fallback (static file served in Chrome on emulator)

    Returns dict with: path, type ('apk'|'web'|'html'), and build_log.
    """
    case_dir = _GENERATED_DIR / case_id
    build_log = []

    if not case_dir.exists():
        return {"error": f"Case directory not found: {case_dir}"}

    # HTML-only app — no build needed
    if (case_dir / "index.html").exists() and not (case_dir / "package.json").exists():
        return {"path": str(case_dir / "index.html"), "type": "html", "build_log": ["HTML app — no build needed"]}

    if not shutil.which("npx"):
        return {"error": "npx not found — install Node.js first"}

    # Step 1: npm install
    ok, out, err = await _run_subprocess(
        ["npm", "install", "--legacy-peer-deps"],
        cwd=str(case_dir), timeout=120, label="npm install",
    )
    build_log.append(f"npm install: {'OK' if ok else 'FAIL'}")
    if not ok:
        return {"error": f"npm install failed: {err[:300]}", "build_log": build_log}

    # Step 2: Try Expo prebuild + gradle (real APK)
    java_available = os.path.exists(_JAVA_HOME) or shutil.which("java")
    if java_available:
        build_log.append("Java found — attempting native Android build")

        # Expo prebuild generates the android/ directory
        ok, out, err = await _run_subprocess(
            ["npx", "expo", "prebuild", "--platform", "android", "--no-install"],
            cwd=str(case_dir), timeout=180, label="expo prebuild",
        )
        build_log.append(f"expo prebuild: {'OK' if ok else 'FAIL'}")

        if ok and (case_dir / "android").exists():
            # Fix Kotlin/Compose version mismatch (Expo SDK 52 bundles 1.5.15 Compose Compiler
            # which requires Kotlin 1.9.25, but gradle resolves 1.9.24)
            gradle_props = case_dir / "android" / "gradle.properties"
            if gradle_props.exists():
                props_text = gradle_props.read_text()
                if "android.kotlinVersion" not in props_text:
                    with open(gradle_props, "a") as f:
                        f.write("\n# Auto-fix: match Kotlin version to cached compiler\n")
                        f.write("android.kotlinVersion=1.9.24\n")
                        f.write("org.gradle.jvmargs=-Xmx4096m -XX:MaxMetaspaceSize=512m\n")
                    build_log.append("Patched gradle.properties (Kotlin version fix)")

            # Gradle build — use release with minification for smaller APK
            gradlew = case_dir / "android" / "gradlew"
            if gradlew.exists():
                gradlew.chmod(0o755)

                # Enable minification for release builds to shrink APK
                app_gradle = case_dir / "android" / "app" / "build.gradle"
                if app_gradle.exists():
                    gradle_text = app_gradle.read_text()
                    if "minifyEnabled false" in gradle_text:
                        gradle_text = gradle_text.replace(
                            "minifyEnabled false",
                            "minifyEnabled true\n            shrinkResources true",
                            1,  # Only replace in release block
                        )
                        app_gradle.write_text(gradle_text)
                        build_log.append("Enabled ProGuard minification for release build")

                # Build release APK (much smaller than debug — typically 10-30MB vs 140MB)
                ok, out, err = await _run_subprocess(
                    [str(gradlew), "assembleRelease", "--no-daemon"],
                    cwd=str(case_dir / "android"), timeout=600, label="gradle assembleRelease",
                )
                if not ok:
                    # Fall back to debug build if release fails (signing issues)
                    build_log.append("Release build failed, trying debug")
                    ok, out, err = await _run_subprocess(
                        [str(gradlew), "assembleDebug", "--no-daemon"],
                        cwd=str(case_dir / "android"), timeout=600, label="gradle assembleDebug",
                    )
                build_log.append(f"gradle build: {'OK' if ok else 'FAIL'}")

                if ok:
                    # Find the APK — prefer release over debug
                    release_apks = list((case_dir / "android").rglob("*-release*.apk"))
                    debug_apks = list((case_dir / "android").rglob("*-debug*.apk"))
                    apk_candidates = release_apks or debug_apks
                    if apk_candidates:
                        apk_path = str(apk_candidates[0])
                        apk_size_mb = os.path.getsize(apk_path) / (1024 * 1024)
                        build_log.append(f"APK: {apk_path} ({apk_size_mb:.1f} MB)")
                        return {"path": apk_path, "type": "apk", "build_log": build_log}
                    build_log.append("WARNING: gradle succeeded but no APK found")

    # Step 3: Expo web export fallback
    build_log.append("Falling back to Expo web export")
    ok, out, err = await _run_subprocess(
        ["npx", "expo", "export", "--platform", "web"],
        cwd=str(case_dir), timeout=180, label="expo export web",
    )
    build_log.append(f"expo export web: {'OK' if ok else 'FAIL'}")

    if ok:
        dist_dir = case_dir / "dist"
        if dist_dir.exists() and (dist_dir / "index.html").exists():
            return {"path": str(dist_dir), "type": "web", "build_log": build_log}

    # Step 4: Generate HTML fallback if nothing else worked
    build_log.append("Generating HTML fallback")
    manifest_path = case_dir / "bug_manifest.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text())
        tmpl_name = manifest.get("template", "ecommerce")
        tmpl = BENCHMARK_TEMPLATES.get(tmpl_name, BENCHMARK_TEMPLATES["ecommerce"])
        html = _generate_html_benchmark(tmpl, manifest.get("bugs", []))
        html_path = case_dir / "index.html"
        html_path.write_text(html, encoding="utf-8")
        return {"path": str(html_path), "type": "html", "build_log": build_log}

    return {"error": "All build strategies failed", "build_log": build_log}


async def install_on_emulator(apk_path: str, device_id: str = "") -> dict:
    """Install an APK on an Android emulator via adb.

    For web/HTML builds, opens the URL in Chrome on the emulator instead.
    """
    if not device_id:
        # Auto-detect
        try:
            result = subprocess.run(
                ["adb", "devices"], capture_output=True, text=True, timeout=10,
                env=_build_env(),
            )
            import re
            matches = re.findall(r"(emulator-\d+)\s+device", result.stdout)
            if matches:
                device_id = matches[0]
            else:
                return {"error": "No emulator device found"}
        except Exception as e:
            return {"error": f"Failed to detect device: {e}"}

    if apk_path.endswith(".apk"):
        # Free up disk space: uninstall old benchmark apps before installing
        try:
            pkg_result = subprocess.run(
                ["adb", "-s", device_id, "shell", "pm", "list", "packages", "-3"],
                capture_output=True, text=True, timeout=10, env=_build_env(),
            )
            for line in pkg_result.stdout.strip().split("\n"):
                pkg = line.replace("package:", "").strip()
                if pkg.startswith("com.benchmark.") or pkg.startswith("com.tastudio."):
                    subprocess.run(
                        ["adb", "-s", device_id, "uninstall", pkg],
                        capture_output=True, text=True, timeout=15, env=_build_env(),
                    )
                    logger.info("Cleaned up old benchmark app: %s", pkg)
        except Exception:
            pass  # Non-fatal — best effort cleanup

        # Real APK install
        try:
            result = subprocess.run(
                ["adb", "-s", device_id, "install", "-r", "-t", apk_path],
                capture_output=True, text=True, timeout=120,
                env=_build_env(),
            )
            if result.returncode == 0:
                apk_size_mb = os.path.getsize(apk_path) / (1024 * 1024)
                return {
                    "installed": True, "device_id": device_id, "type": "apk",
                    "path": apk_path, "size_mb": round(apk_size_mb, 1),
                }
            return {"error": f"adb install failed: {result.stderr[:300]}"}
        except Exception as e:
            return {"error": f"Install failed: {e}"}
    else:
        # Web/HTML — serve via backend and open in Chrome
        return {
            "installed": True,
            "device_id": device_id,
            "type": "web",
            "note": "Web app — will be served via backend and opened in Chrome on emulator",
        }


# ---------------------------------------------------------------------------
# Benchmark harness — the full generate → build → install → QA → score loop
# ---------------------------------------------------------------------------

async def run_benchmark(
    case_id: str,
    thread_mode: str = "fresh",
    device_id: Optional[str] = None,
) -> BenchmarkRun:
    """Run the complete benchmark loop:
    1. Build the app (APK or web bundle)
    2. Install on emulator (APK) or serve locally (web)
    3. Run retention.run_web_flow or retention.run_android_flow
    4. Wait for completion
    5. Score against planted bug manifest (precision/recall/F1)
    6. Persist lineage

    Args:
        case_id: Benchmark case to run
        thread_mode: 'fresh' (new agent thread) or 'continuous' (same thread)
        device_id: ADB device ID (auto-detect if omitted)
    """
    import time as _time

    run_id = f"benchrun-{uuid.uuid4().hex[:8]}"
    t0 = _time.time()
    run = BenchmarkRun(
        run_id=run_id,
        case_id=case_id,
        started_at=_now_iso(),
        thread_mode=thread_mode,
    )

    case_dir = _GENERATED_DIR / case_id
    manifest_path = case_dir / "bug_manifest.json"

    if not manifest_path.exists():
        run.status = "failed"
        run.phases["error"] = {"message": f"Bug manifest not found for {case_id}"}
        _save_run(run.to_dict())
        return run

    manifest = json.loads(manifest_path.read_text())
    run.bugs_planted = manifest.get("total_planted_bugs", 0)

    # ── Phase 1: BUILD ────────────────────────────────────────────────────
    run.phases["build"] = {"started_at": _now_iso()}
    build_result = await build_android_apk(case_id)
    run.phases["build"]["result"] = build_result
    run.phases["build"]["completed_at"] = _now_iso()

    if build_result.get("error"):
        run.status = "failed"
        run.phases["build"]["error"] = build_result["error"]
        _save_run(run.to_dict())
        return run

    build_type = build_result.get("type", "html")
    build_path = build_result.get("path", "")
    logger.info(f"Benchmark {run_id}: built {build_type} at {build_path}")

    # ── Phase 2: INSTALL ──────────────────────────────────────────────────
    run.phases["install"] = {"started_at": _now_iso()}

    if build_type == "apk":
        install_result = await install_on_emulator(build_path, device_id or "")
        run.phases["install"]["result"] = install_result
        if install_result.get("error"):
            # APK install failed (e.g. disk space) — fall back to HTML serving
            logger.warning(
                "APK install failed for %s: %s — falling back to HTML serving",
                case_id, install_result["error"][:100],
            )
            build_type = "html"
            run.phases["install"]["fallback"] = "html"
            # Generate HTML fallback
            manifest = json.loads(manifest_path.read_text())
            tmpl = BENCHMARK_TEMPLATES.get(manifest.get("template", "ecommerce"),
                                           BENCHMARK_TEMPLATES["ecommerce"])
            html = _generate_html_benchmark(tmpl, manifest.get("bugs", []))
            html_path = case_dir / "index.html"
            html_path.write_text(html, encoding="utf-8")
            install_result = {"installed": True, "type": "html", "fallback_reason": "APK install failed"}
            run.phases["install"]["result"] = install_result
            build_path = str(html_path)
        device_id = install_result.get("device_id", device_id)
    else:
        # Web/HTML — determine the URL to serve
        install_result = {"installed": True, "type": build_type}
        run.phases["install"]["result"] = install_result

    run.phases["install"]["completed_at"] = _now_iso()

    # ── Phase 3: DETERMINE APP URL ────────────────────────────────────────
    backend_base = os.environ.get("TA_BACKEND_URL", "http://localhost:8000")
    emulator_base = "http://10.0.2.2:8000"  # Android emulator → host machine

    if build_type == "apk":
        # APK is installed — use package name for retention.run_android_flow
        app_url = None
        app_package = manifest.get("package_name") or f"com.benchmark.{case_id.replace('-', '_')}"
    elif build_type == "web":
        # Serve the dist directory
        dist_dir = Path(build_path)
        from ..api.demo import _generated_apps
        _generated_apps[case_id] = dist_dir
        app_url = f"{emulator_base}/api/demo/showcase-app/{case_id}"
        app_package = None
    else:
        # HTML — serve the case directory
        from ..api.demo import _generated_apps
        _generated_apps[case_id] = case_dir
        app_url = f"{emulator_base}/api/demo/showcase-app/{case_id}"
        app_package = None

    # ── Phase 4: RUN QA ───────────────────────────────────────────────────
    run.phases["qa"] = {"started_at": _now_iso()}

    try:
        from ..api.mcp_pipeline import dispatch_qa_verification

        if app_url:
            qa_result = await dispatch_qa_verification(
                "retention.run_web_flow",
                {
                    "url": app_url.replace("10.0.2.2", "localhost"),  # dispatch validates then rewrites
                    "app_name": manifest.get("app_name", case_id),
                    "timeout_seconds": 600,  # Benchmark runs need longer timeout
                },
            )
        elif app_package:
            qa_result = await dispatch_qa_verification(
                "retention.run_android_flow",
                {
                    "app_package": app_package,
                    "app_name": manifest.get("app_name", case_id),
                    "device_id": device_id,
                    "timeout_seconds": 600,
                },
            )
        else:
            qa_result = {"error": "No app URL or package for QA"}

        qa_run_id = qa_result.get("run_id")
        if qa_run_id:
            run.qa_run_ids.append(qa_run_id)
        run.phases["qa"]["qa_run_id"] = qa_run_id
        run.phases["qa"]["flow_type"] = "android" if app_package else "web"
        run.phases["qa"]["app_url"] = app_url
        run.phases["qa"]["app_package"] = app_package

    except Exception as e:
        run.phases["qa"]["error"] = str(e)

    run.phases["qa"]["completed_at"] = _now_iso()

    # ── Finalize ──────────────────────────────────────────────────────────
    run.total_time_s = round(_time.time() - t0, 2)
    run.status = "running" if run.qa_run_ids else "failed"
    run.completed_at = _now_iso()

    # Persist lineage
    _save_run(run.to_dict())

    # Write context handoff files for agent continuity
    _write_handoff(run, manifest)

    logger.info(
        f"Benchmark {run_id}: status={run.status}, build={build_type}, "
        f"qa_runs={run.qa_run_ids}, time={run.total_time_s}s"
    )
    return run


def _write_handoff(run: BenchmarkRun, manifest: dict) -> None:
    """Write context management files for agent continuity."""
    handoff_dir = _GENERATED_DIR / run.case_id / "handoff"
    handoff_dir.mkdir(parents=True, exist_ok=True)

    # CURRENT_STATE.md
    (handoff_dir / "CURRENT_STATE.md").write_text(f"""# Benchmark State — {run.run_id}

- **Case:** {run.case_id}
- **Status:** {run.status}
- **Thread mode:** {run.thread_mode}
- **Bugs planted:** {run.bugs_planted}
- **QA run IDs:** {', '.join(run.qa_run_ids) or 'none'}
- **Total time:** {run.total_time_s}s
- **Build type:** {run.phases.get('build', {}).get('result', {}).get('type', '?')}
""")

    # RUN_LOG.md
    (handoff_dir / "RUN_LOG.md").write_text(f"""# Run Log — {run.run_id}

| Phase | Status | Duration |
|-------|--------|----------|
| Build | {run.phases.get('build', {}).get('result', {}).get('type', 'unknown')} | {run.phases.get('build', {}).get('completed_at', '')} |
| Install | {'OK' if run.phases.get('install', {}).get('result', {}).get('installed') else 'FAIL'} | {run.phases.get('install', {}).get('completed_at', '')} |
| QA | {'started' if run.qa_run_ids else 'not started'} | {run.phases.get('qa', {}).get('completed_at', '')} |
""")

    # BENCHMARK_CASE.json — machine-readable
    (handoff_dir / "BENCHMARK_CASE.json").write_text(json.dumps({
        "run_id": run.run_id,
        "case_id": run.case_id,
        "bugs_planted": run.bugs_planted,
        "bug_manifest": manifest.get("bugs", []),
        "qa_run_ids": run.qa_run_ids,
        "phases": run.phases,
        "thread_mode": run.thread_mode,
    }, indent=2, default=str))


# ---------------------------------------------------------------------------
# Scoring — precision/recall/F1 for planted bug detection
# ---------------------------------------------------------------------------

def score_benchmark(run_id: str) -> dict:
    """Score a completed benchmark run against the planted bug manifest.

    Returns precision, recall, F1, and per-bug detection status.
    """
    runs = _load_runs()
    run_data = next((r for r in runs if r.get("run_id") == run_id), None)
    if not run_data:
        return {"error": f"Run not found: {run_id}"}

    case_id = run_data.get("case_id")
    manifest_path = _GENERATED_DIR / case_id / "bug_manifest.json"
    if not manifest_path.exists():
        return {"error": f"Bug manifest not found for case {case_id}"}

    manifest = json.loads(manifest_path.read_text())
    planted_bugs = manifest.get("bugs", [])
    total_planted = len(planted_bugs)

    # Get QA results from pipeline
    from ..api.mcp_pipeline import _get_run_result

    detected_bugs = []
    false_positives = []
    total_reported = 0

    for qa_run_id in run_data.get("qa_run_ids", []):
        result = _get_run_result(qa_run_id)
        if not result:
            continue

        execution = result.get("execution", {})
        for tr in execution.get("results", []):
            if tr.get("status") != "pass":
                total_reported += 1

                # Match against planted bugs using keyword overlap
                matched = _match_failure_to_planted_bug(tr, planted_bugs)
                if matched:
                    detected_bugs.append(matched)
                else:
                    false_positives.append({
                        "test_id": tr.get("test_id"),
                        "name": tr.get("name"),
                        "status": tr.get("status"),
                    })

    true_positives = len(set(b["bug_id"] for b in detected_bugs))
    precision = true_positives / total_reported if total_reported > 0 else 0.0
    recall = true_positives / total_planted if total_planted > 0 else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0

    return {
        "run_id": run_id,
        "case_id": case_id,
        "bugs_planted": total_planted,
        "bugs_detected": true_positives,
        "total_reported": total_reported,
        "false_positives": len(false_positives),
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
        "per_bug": [
            {
                "bug_id": b["bug_id"],
                "name": b.get("name", b.get("description", "")),
                "severity": b.get("severity", "medium"),
                "detected": b["bug_id"] in {d["bug_id"] for d in detected_bugs},
            }
            for b in planted_bugs
        ],
        "false_positive_details": false_positives[:10],
    }


def _match_failure_to_planted_bug(failure: dict, planted_bugs: List[dict]) -> Optional[dict]:
    """Try to match a test failure to a planted bug using keyword overlap."""
    failure_text = (
        f"{failure.get('name', '')} {failure.get('error', '')} "
        f"{json.dumps(failure.get('step_results', []))}"
    ).lower()

    for bug in planted_bugs:
        keywords = bug.get("detection_keywords", [])
        if not keywords:
            # Generate keywords from description
            keywords = bug.get("description", "").lower().split()

        matches = sum(1 for kw in keywords if kw.lower() in failure_text)
        if matches >= 2 or (matches >= 1 and len(keywords) <= 3):
            return bug

    return None


# ---------------------------------------------------------------------------
# LLM prompts for app generation
# ---------------------------------------------------------------------------

_EXPO_SYSTEM_PROMPT = """You are a React Native / Expo expert. Generate complete, runnable Expo app source code.

Output format: for each file, use this header format:
=== FILE: path/to/file.tsx ===
(file content here)

Generate a complete Expo Router app with TypeScript. Include:
- app/_layout.tsx (root layout with tab navigation)
- app/(tabs)/_layout.tsx (tab bar layout)
- One file per screen in app/(tabs)/
- Shared components in components/
- Use @expo/vector-icons for icons
- Use React Native core components only (no external UI libraries)
- Make it visually polished with proper styling

IMPORTANT: When asked to plant bugs, embed them naturally in the code. The bugs should be
realistic development mistakes, not obvious sabotage. Add a comment /* PLANTED_BUG: BUG-XXX */
near each planted bug for verification, but make the bug itself look like a genuine mistake."""


def _build_generation_prompt(template: dict, planted_bugs: List[dict], change_requests: Optional[List[str]]) -> str:
    """Build the LLM prompt for generating the benchmark app."""
    screens_str = ", ".join(template["screens"])
    workflows_str = "\n".join(f"  - {w}" for w in template["workflows"])
    bugs_str = "\n".join(
        f"  - {b['bug_id']}: {b['description']} (on screen: {b.get('screen', 'any')}, severity: {b['severity']})"
        for b in planted_bugs
    )

    prompt = f"""Generate a complete Expo React Native app: **{template['name']}**

Description: {template['description']}

Screens: {screens_str}

Key workflows:
{workflows_str}

PLANTED BUGS — embed these {len(planted_bugs)} bugs naturally in the code:
{bugs_str}

Make each bug look like a realistic development mistake. Mark each with a comment /* PLANTED_BUG: BUG-XXX */."""

    if change_requests:
        cr_str = "\n".join(f"  - {cr}" for cr in change_requests)
        prompt += f"""

CHANGE REQUESTS — these features should NOT be implemented yet (they are the "after" in before/after eval):
{cr_str}
Add TODO comments where these features would go."""

    return prompt


def _parse_generated_source(source: str) -> Dict[str, str]:
    """Parse LLM-generated source code into file dict."""
    files = {}
    current_file = None
    current_content = []

    for line in source.split("\n"):
        if line.startswith("=== FILE:") and line.endswith("==="):
            if current_file:
                files[current_file] = "\n".join(current_content)
            current_file = line.replace("=== FILE:", "").replace("===", "").strip()
            current_content = []
        elif line.startswith("```") and current_file is None:
            # Skip markdown fences at the top level
            continue
        else:
            current_content.append(line)

    if current_file:
        files[current_file] = "\n".join(current_content)

    return files


def _build_nav_items(screens: List[str]) -> str:
    """Build nav bar HTML items (extracted to avoid f-string backslash issues)."""
    items = []
    for s in screens:
        label = s.replace("_", " ").title()[:8]
        items.append(f'<div class="nav-item" onclick="showScreen(\'{s}\')">{label}</div>')
    return "".join(items)


def _generate_html_benchmark(template: dict, planted_bugs: List[dict]) -> str:
    """Generate a self-contained HTML benchmark app (fallback when Expo/LLM unavailable)."""
    screens_html = ""
    for screen in template["screens"]:
        screens_html += f'<div class="screen" id="{screen}"><h2>{screen.replace("_", " ").title()}</h2>'
        screens_html += '<div class="content">Sample content for this screen</div></div>\n'

    bugs_js = ""
    for bug in planted_bugs:
        if bug["type"] == "missing_handler":
            bugs_js += f'// {bug["bug_id"]}: {bug["description"]}\n'
        elif bug["type"] == "wrong_text":
            bugs_js += f'// {bug["bug_id"]}: {bug["description"]}\n'

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
<title>{template['name']}</title>
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{ font-family: -apple-system, sans-serif; background: #f5f5f5; max-width: 430px; margin: 0 auto; min-height: 100vh; }}
.header {{ background: #1a1a2e; color: white; padding: 16px; text-align: center; font-size: 18px; font-weight: 600; }}
.screen {{ padding: 16px; }}
.screen h2 {{ font-size: 20px; margin-bottom: 12px; color: #333; }}
.content {{ background: white; border-radius: 12px; padding: 16px; margin-bottom: 12px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }}
.nav {{ display: flex; justify-content: space-around; background: white; border-top: 1px solid #e0e0e0; padding: 8px 0; position: fixed; bottom: 0; left: 0; right: 0; max-width: 430px; margin: 0 auto; }}
.nav-item {{ text-align: center; font-size: 11px; color: #666; cursor: pointer; padding: 4px 12px; }}
.nav-item.active {{ color: #1a1a2e; font-weight: 600; }}
.btn {{ background: #1a1a2e; color: white; border: none; padding: 12px 24px; border-radius: 8px; font-size: 14px; cursor: pointer; width: 100%; margin-top: 8px; }}
.input {{ width: 100%; padding: 12px; border: 1px solid #ddd; border-radius: 8px; font-size: 14px; margin-bottom: 8px; }}
</style>
</head>
<body>
<div class="header">{template['name']}</div>
{screens_html}
<div class="nav">
{_build_nav_items(template['screens'][:5])}
</div>
<script>
{bugs_js}
function showScreen(id) {{
  document.querySelectorAll('.screen').forEach(s => s.style.display = 'none');
  const el = document.getElementById(id);
  if (el) el.style.display = 'block';
}}
// Show first screen
document.querySelectorAll('.screen').forEach((s, i) => {{ if (i > 0) s.style.display = 'none'; }});
</script>
</body>
</html>"""


# ---------------------------------------------------------------------------
# MCP tool dispatchers
# ---------------------------------------------------------------------------

async def dispatch_benchmark_gen(tool: str, args: Dict[str, Any]) -> Any:
    """Handle retention.benchmark.generate_app and related tools."""

    if tool == "retention.benchmark.generate_app":
        template = args.get("template", "ecommerce")
        difficulty = args.get("difficulty", "medium")
        num_bugs = int(args.get("num_bugs", 5))
        change_requests = args.get("change_requests")
        if isinstance(change_requests, str):
            change_requests = [cr.strip() for cr in change_requests.split(",")]

        case = await generate_benchmark_app(
            template=template,
            difficulty=difficulty,
            num_bugs=num_bugs,
            change_requests=change_requests,
        )
        return {
            "case_id": case.case_id,
            "app_name": case.app_name,
            "template": case.template,
            "difficulty": case.difficulty,
            "planted_bugs": len(case.planted_bugs),
            "source_path": case.source_path,
            "status": case.status,
            "message": (
                f"Generated {case.app_name} with {len(case.planted_bugs)} planted bugs "
                f"({case.difficulty} difficulty). Use retention.benchmark.run_case to start the QA benchmark."
            ),
        }

    if tool == "retention.benchmark.list_templates":
        return {
            "templates": [
                {"id": k, "name": v["name"], "description": v["description"],
                 "screens": len(v["screens"]), "workflows": len(v["workflows"])}
                for k, v in BENCHMARK_TEMPLATES.items()
            ],
            "difficulty_levels": list(BUG_PROFILES.keys()) + ["mixed"],
        }

    if tool == "retention.benchmark.list_cases":
        cases = _load_cases()
        return {"cases": cases, "count": len(cases)}

    if tool == "retention.benchmark.run_case":
        case_id = args.get("case_id")
        if not case_id:
            return {"error": "case_id is required"}
        thread_mode = args.get("thread_mode", "fresh")
        run = await run_benchmark(case_id, thread_mode=thread_mode)
        return run.to_dict()

    if tool == "retention.benchmark.score":
        run_id = args.get("run_id")
        if not run_id:
            return {"error": "run_id is required"}
        return score_benchmark(run_id)

    if tool == "retention.benchmark.run_history":
        runs = _load_runs()
        return {"runs": runs, "count": len(runs)}

    raise ValueError(f"Unknown benchmark gen tool: {tool}")
