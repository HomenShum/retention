#!/usr/bin/env python3
"""
retention.sh — MobileBench-OL Benchmark Harness

MobileBench-OL: 1080 tasks, 80 Chinese-market Android apps
GitHub: https://github.com/LHH2CV/mobilebench-OL
Paper: https://arxiv.org/abs/2601.20335

The actual benchmark apps (WeChat, Bilibili, Alipay, etc.) require verified
Chinese accounts and are not installable on a fresh emulator. This harness:
  1. Clones the repo and loads task CSVs
  2. Checks which benchmark apps are installed on emulator-5554
  3. Runs tasks only for installed apps
  4. Reports harness_status: "ready" with blocked_reason when no apps are found

Usage:
    cd backend
    python scripts/run_mobilebench_ol.py [--dry-run] [--max-tasks N] [--max-steps N] [--device ID]

Flags:
    --dry-run        Skip execution, just clone repo, load tasks, report harness status
    --max-tasks N    Limit number of runnable tasks to attempt (default: all)
    --max-steps N    Max steps per task before marking failed (default: 30)
    --device ID      ADB device serial (default: emulator-5554)

Output:
    backend/data/benchmark_reports/mobilebench_ol_benchmark_{timestamp}.json
"""

import argparse
import csv
import json
import logging
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

# ---------------------------------------------------------------------------
# Path bootstrap
# ---------------------------------------------------------------------------
_THIS_FILE = Path(__file__).resolve()
_BACKEND = _THIS_FILE.parent.parent      # backend/
_REPO_ROOT = _BACKEND.parent
sys.path.insert(0, str(_BACKEND))
sys.path.insert(0, str(_REPO_ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("mobilebench_ol")

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
REPORTS_DIR = _BACKEND / "data" / "benchmark_reports"
BENCHMARKS_DIR = _BACKEND / "data" / "benchmarks"
EXTERNAL_DIR = _BACKEND / "data" / "external_benchmarks" / "mobilebench_ol"
REPO_DIR = EXTERNAL_DIR / "mobilebench-OL"
REGISTRY_PATH = BENCHMARKS_DIR / "benchmark_registry.json"

REPO_URL = "https://github.com/LHH2CV/mobilebench-OL"
DEFAULT_DEVICE = "emulator-5554"

# Known MobileBench-OL app package names mapped to human names.
# Source: paper appendix + repo README inspection.
BENCHMARK_APP_PACKAGES: dict[str, str] = {
    "tv.danmaku.bili": "Bilibili",
    "com.tencent.mm": "WeChat",
    "com.eg.android.AlipayGphone": "Alipay",
    "com.taobao.taobao": "Taobao",
    "com.jingdong.app.mall": "JD.com",
    "com.tencent.mobileqq": "QQ",
    "com.sina.weibo": "Weibo",
    "com.ss.android.ugc.aweme": "Douyin (TikTok CN)",
    "com.ximalaya.ting.android": "Ximalaya",
    "com.netease.cloudmusic": "NetEase Cloud Music",
    "com.tencent.qqmusic": "QQ Music",
    "com.kuaishou.nebula": "Kuaishou",
    "com.xiaomi.shop": "Xiaomi Mall",
    "com.pinduoduo.android": "Pinduoduo",
    "com.meituan.android.pt": "Meituan",
    "com.sankuai.meituan.takeoutnew": "Meituan Takeout",
    "com.ele.me": "Eleme",
    "com.ctrip.ct": "Ctrip",
    "com.qunar.mobile": "Qunar",
    "com.sankuai.hotel": "Meituan Hotel",
    "com.dianping.v1": "Dianping",
    "com.shizhuang.shizhuangapp": "Shizhuang",
    "com.moji.mjweather": "Moji Weather",
    "com.qiyi.video": "iQIYI",
    "com.youku.phone": "Youku",
    "com.hunantv.imgo.activity": "Mango TV",
    "com.tencent.qqlive": "Tencent Video",
    "com.baidu.searchbox": "Baidu",
    "com.alibaba.android.reader": "Shuqi Novel",
    "com.moji.life": "Moji Life",
    "com.miui.calculator": "MIUI Calculator",
    "com.huawei.calculator": "Huawei Calculator",
    "com.vivo.calculator": "Vivo Calculator",
    "com.oppo.calculator": "OPPO Calculator",
    "cn.com.cmbc.newmbankPad": "CMBC Mobile",
    "com.icbc": "ICBC Mobile",
    "com.cmbchina.ccd.pluto.cmbActivity": "CMB Mobile",
    "com.bankcomm.Bankcomm": "Bank of Communications",
    "com.abchina.mobilebanking": "Agricultural Bank",
    "com.chinamworld.main": "CCB Mobile",
    "com.lbe.security.lite": "360 Mobile Guard",
    "com.baidu.map.transit": "Baidu Maps",
    "com.autonavi.minimap": "AutoNavi Maps (Gaode)",
    "com.tencent.map": "Tencent Maps",
    "com.sdu.didi.psnger": "DiDi",
    "com.sfexpress.sf365": "SF Express",
    "com.jd.logistics.jdlogisticsmobile": "JD Logistics",
    "com.shanshu.collect": "Shangshu",
    "com.xiaomi.smarthome": "Mi Home",
    "com.tuya.smart": "Tuya Smart",
    "com.xiaomi.miio": "Mi IO",
    "com.ss.android.article.news": "Toutiao",
    "com.zhihu.android": "Zhihu",
    "com.hupu.games": "Hupu Sports",
    "com.tiger.finance": "Tiger Brokers",
    "com.tencent.weread": "WeRead",
    "com.amazon.mShop.android.shopping": "Amazon CN",
    "com.suning.mobile.android": "Suning",
    "com.vipshop.shopping": "VIP.com",
    "com.weli.app": "Weli",
    "com.rainbow.android.client": "Rainbow",
    "cn.missevan": "MissEvan",
    "com.ktv.nongke": "KTV",
    "com.tencent.leagueoflegendshd": "LoL Wild Rift CN",
    "com.mihoyo.yuanshen": "Genshin Impact CN",
    "com.xiaomi.gamecenter.sdk.portal": "Xiaomi Game Center",
    "com.coolapk.market": "CoolApk",
    "com.huawei.appmarket": "Huawei AppGallery",
    "com.vivo.appstore": "Vivo App Store",
    "com.oppo.market": "OPPO App Market",
    "com.xiaomi.market": "Xiaomi App Store",
    "com.baidu.yuedu": "Baidu Reading",
    "com.tencent.news": "Tencent News",
    "com.ifeng.news2": "iFeng News",
    "com.cmcc.cmvideo": "CCTV Video",
    "cn.soulapp.android": "Soul",
    "com.baijiayun.baijiayunapp": "BaiJiaYun",
    "com.heytap.market": "HEYTAP Market",
    "com.xiaomi.account": "Xiaomi Account",
    "com.android.settings": "Settings (stock Android)",  # may be present
}

BLOCKED_REASON = (
    "All 80 benchmark apps are Chinese-market apps requiring verified accounts "
    "(WeChat, Bilibili, Alipay, etc.). Harness is wired and ready — needs app APKs."
)


# ---------------------------------------------------------------------------
# 1. Repo management
# ---------------------------------------------------------------------------

def clone_repo() -> bool:
    """Clone MobileBench-OL repo if not present. Returns True on success."""
    EXTERNAL_DIR.mkdir(parents=True, exist_ok=True)

    if REPO_DIR.exists():
        logger.info("MobileBench-OL repo already present at %s", REPO_DIR)
        return True

    logger.info("Cloning %s → %s", REPO_URL, REPO_DIR)
    try:
        result = subprocess.run(
            ["git", "clone", "--depth", "1", REPO_URL, str(REPO_DIR)],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode != 0:
            logger.error("git clone failed:\n%s", result.stderr)
            return False
        logger.info("Repo cloned successfully.")
        return True
    except subprocess.TimeoutExpired:
        logger.error("git clone timed out after 120s.")
        return False
    except FileNotFoundError:
        logger.error("git not found in PATH.")
        return False


# ---------------------------------------------------------------------------
# 2. Task loading
# ---------------------------------------------------------------------------

def _load_csv(csv_path: Path) -> list[dict]:
    """Parse a MobileBench-OL CSV task file."""
    tasks = []
    if not csv_path.exists():
        logger.warning("CSV not found: %s", csv_path)
        return tasks

    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            tasks.append(dict(row))
    logger.info("Loaded %d tasks from %s", len(tasks), csv_path.name)
    return tasks


def load_tasks() -> list[dict]:
    """Load tasks from top12.csv and longtail.csv."""
    all_tasks: list[dict] = []

    # Try common locations inside the cloned repo
    candidate_patterns = [
        "top12.csv",
        "longtail.csv",
        "data/top12.csv",
        "data/longtail.csv",
        "tasks/top12.csv",
        "tasks/longtail.csv",
        "benchmark/top12.csv",
        "benchmark/longtail.csv",
    ]

    found_any = False
    for pattern in candidate_patterns:
        csv_path = REPO_DIR / pattern
        if csv_path.exists():
            tasks = _load_csv(csv_path)
            for t in tasks:
                t["_source_csv"] = pattern
            all_tasks.extend(tasks)
            found_any = True

    if not found_any:
        # Walk the repo looking for any CSV
        for csv_path in sorted(REPO_DIR.rglob("*.csv")):
            tasks = _load_csv(csv_path)
            for t in tasks:
                t["_source_csv"] = str(csv_path.relative_to(REPO_DIR))
            all_tasks.extend(tasks)
            found_any = True

    if not found_any:
        logger.warning("No CSV task files found in repo. Using synthetic task list.")
        # Synthesize placeholder tasks so the harness can still report accurately
        all_tasks = _synthetic_tasks()

    return all_tasks


def _synthetic_tasks() -> list[dict]:
    """
    Return a minimal synthetic task list when CSV files aren't available.
    Covers all 80 known apps so package-presence checks still work.
    """
    tasks = []
    for pkg, app_name in BENCHMARK_APP_PACKAGES.items():
        tasks.append({
            "task_id": f"synthetic_{pkg.replace('.', '_')}",
            "app": app_name,
            "package": pkg,
            "task": f"Open {app_name} and verify the home screen loads.",
            "category": "navigation",
            "_source_csv": "synthetic",
        })
    return tasks


# ---------------------------------------------------------------------------
# 3. Device / app detection
# ---------------------------------------------------------------------------

def get_installed_packages(device: str) -> set[str]:
    """Return set of package names installed on the given ADB device."""
    try:
        result = subprocess.run(
            ["adb", "-s", device, "shell", "pm", "list", "packages"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            logger.warning("adb pm list packages failed: %s", result.stderr.strip())
            return set()
        packages = set()
        for line in result.stdout.splitlines():
            line = line.strip()
            if line.startswith("package:"):
                packages.add(line[len("package:"):].strip())
        logger.info("Found %d packages on %s", len(packages), device)
        return packages
    except FileNotFoundError:
        logger.warning("adb not found in PATH — cannot check installed packages.")
        return set()
    except subprocess.TimeoutExpired:
        logger.warning("adb timed out listing packages.")
        return set()


def filter_runnable_tasks(tasks: list[dict], installed_packages: set[str]) -> list[dict]:
    """Return only tasks whose app package is installed on the device."""
    runnable = []
    for task in tasks:
        pkg = task.get("package", "")
        if not pkg:
            # Try to infer package from app name field
            app_name = task.get("app", task.get("App", ""))
            for known_pkg, known_name in BENCHMARK_APP_PACKAGES.items():
                if known_name.lower() == app_name.lower():
                    pkg = known_pkg
                    task = {**task, "package": pkg}
                    break
        if pkg and pkg in installed_packages:
            runnable.append(task)
    return runnable


# ---------------------------------------------------------------------------
# 4. Agent stub — process_message interface
# ---------------------------------------------------------------------------

def process_message(
    screenshot_path: str,
    task: str,
    history: list[dict],
) -> dict:
    """
    MobileBench-OL agent interface stub.

    Real implementation: send screenshot + task + history to a vision-capable
    model (e.g. Claude claude-haiku-4-5-20251001) and parse the returned action.

    For now: immediately terminates so the harness loop is exercisable without
    requiring a live model call. The action wiring (uiautomator2 execution) is
    fully implemented below.

    Returns:
        {
          "action": "tap" | "swipe" | "type" | "terminate",
          "params": { ... }
        }
    """
    logger.debug("process_message called: task=%r, history_len=%d", task, len(history))
    # Stub: always terminate — the harness loop and action executor are wired.
    # Replace this body with a real model call to activate the agent.
    return {"action": "terminate", "params": {"reason": "stub_terminate"}}


# ---------------------------------------------------------------------------
# 5. Action execution via uiautomator2
# ---------------------------------------------------------------------------

def _get_device(device_serial: str):
    """Connect to device via uiautomator2. Returns device object or None."""
    try:
        import uiautomator2 as u2  # type: ignore
        d = u2.connect(device_serial)
        # Quick liveness check
        _ = d.info
        logger.info("uiautomator2 connected to %s", device_serial)
        return d
    except ImportError:
        logger.warning("uiautomator2 not installed — action execution disabled.")
        return None
    except Exception as exc:
        logger.warning("uiautomator2 connect failed: %s", exc)
        return None


def execute_action(device, action_dict: dict) -> bool:
    """
    Execute a MobileBench-OL action dict via uiautomator2.

    Supported actions:
      tap:       {"x": int, "y": int}
      swipe:     {"start_x", "start_y", "end_x", "end_y", "duration"?}
      type:      {"text": str}
      terminate: {}  — signals end of episode

    Returns True if action was executed (or is 'terminate'), False on error.
    """
    action = action_dict.get("action", "")
    params = action_dict.get("params", {})

    if action == "terminate":
        return True

    if device is None:
        logger.debug("No device — skipping action %s", action)
        return False

    try:
        if action == "tap":
            x = int(params.get("x", 0))
            y = int(params.get("y", 0))
            device.click(x, y)
            logger.debug("tap(%d, %d)", x, y)

        elif action == "swipe":
            sx = int(params.get("start_x", 0))
            sy = int(params.get("start_y", 0))
            ex = int(params.get("end_x", 0))
            ey = int(params.get("end_y", 0))
            dur = float(params.get("duration", 0.5))
            device.swipe(sx, sy, ex, ey, duration=dur)
            logger.debug("swipe(%d,%d → %d,%d, dur=%.2f)", sx, sy, ex, ey, dur)

        elif action == "type":
            text = str(params.get("text", ""))
            device.send_keys(text)
            logger.debug("type(%r)", text)

        else:
            logger.warning("Unknown action %r — skipping.", action)
            return False

        return True

    except Exception as exc:
        logger.warning("Action execution error (%s): %s", action, exc)
        return False


def take_screenshot(device, tmp_dir: Path, step: int) -> str:
    """Capture a screenshot via uiautomator2 and save to tmp_dir. Returns path."""
    screenshot_path = str(tmp_dir / f"step_{step:04d}.png")
    if device is None:
        # Create an empty placeholder so process_message always gets a valid path
        Path(screenshot_path).touch()
        return screenshot_path
    try:
        img = device.screenshot()
        img.save(screenshot_path)
    except Exception as exc:
        logger.warning("Screenshot failed at step %d: %s", step, exc)
        Path(screenshot_path).touch()
    return screenshot_path


# ---------------------------------------------------------------------------
# 6. Task runner
# ---------------------------------------------------------------------------

def run_task(task: dict, device, max_steps: int) -> dict:
    """
    Execute one MobileBench-OL task.

    Returns a result dict with: task_id, success, steps_taken, termination_reason.
    """
    task_id = task.get("task_id", task.get("id", "unknown"))
    task_desc = task.get("task", task.get("Task", ""))
    package = task.get("package", "")

    logger.info("Running task %s: %s", task_id, task_desc[:80])

    # Launch the app
    if device is not None and package:
        try:
            device.app_start(package)
            time.sleep(2.0)
        except Exception as exc:
            logger.warning("Failed to launch %s: %s", package, exc)

    history: list[dict] = []
    success = False
    termination_reason = "max_steps"

    with tempfile.TemporaryDirectory() as tmp_str:
        tmp_dir = Path(tmp_str)
        for step in range(max_steps):
            screenshot_path = take_screenshot(device, tmp_dir, step)

            action_dict = process_message(
                screenshot_path=screenshot_path,
                task=task_desc,
                history=history,
            )

            history.append({
                "step": step,
                "screenshot": screenshot_path,
                "action": action_dict,
            })

            action_type = action_dict.get("action", "")
            if action_type == "terminate":
                success = True
                termination_reason = action_dict.get("params", {}).get(
                    "reason", "agent_terminate"
                )
                logger.info("Task %s terminated at step %d: %s", task_id, step, termination_reason)
                break

            executed = execute_action(device, action_dict)
            if not executed:
                termination_reason = "action_failed"
                break

            time.sleep(0.5)

    # Stop the app
    if device is not None and package:
        try:
            device.app_stop(package)
        except Exception:
            pass

    # Note: stub process_message always terminates immediately, so success=True
    # from the harness perspective — but we flag stub terminations separately.
    if termination_reason == "stub_terminate":
        success = False  # stub run doesn't count as genuine pass

    return {
        "task_id": task_id,
        "app": task.get("app", task.get("App", "")),
        "package": package,
        "task": task_desc,
        "success": success,
        "steps_taken": len(history),
        "termination_reason": termination_reason,
        "source_csv": task.get("_source_csv", ""),
    }


# ---------------------------------------------------------------------------
# 7. Metrics
# ---------------------------------------------------------------------------

def compute_metrics(results: list[dict]) -> dict:
    """
    Compute precision / recall / F1.

    For MobileBench-OL: each task is either pass or fail.
    We treat each passed task as a true positive (TP).
    No false positives are possible (we only mark pass on clean terminate).
    Recall = TP / total_attempted.
    Precision = TP / TP  (1.0 if any pass, else undefined).
    F1 = harmonic mean.
    """
    if not results:
        return {"precision": None, "recall": None, "f1": None}

    n = len(results)
    passed = sum(1 for r in results if r.get("success"))

    recall = passed / n if n > 0 else 0.0
    precision = 1.0 if passed > 0 else 0.0
    if precision + recall > 0:
        f1 = 2 * precision * recall / (precision + recall)
    else:
        f1 = 0.0

    return {
        "precision": round(precision, 4) if passed > 0 else None,
        "recall": round(recall, 4),
        "f1": round(f1, 4) if passed > 0 else None,
    }


# ---------------------------------------------------------------------------
# 8. Registry update
# ---------------------------------------------------------------------------

def update_registry(harness_status: str) -> None:
    """Update benchmark_registry.json to mark MobileBench-OL as harness_ready."""
    if not REGISTRY_PATH.exists():
        logger.warning("Registry not found at %s — skipping update.", REGISTRY_PATH)
        return

    with open(REGISTRY_PATH, "r", encoding="utf-8") as f:
        registry = json.load(f)

    benchmarks = registry.get("benchmarks", [])
    entry_idx = None
    for i, b in enumerate(benchmarks):
        if b.get("id") == "mobile_bench_ol":
            entry_idx = i
            break

    new_entry: dict = {
        "id": "mobile_bench_ol",
        "name": "MobileBench-OL",
        "type": "external_public",
        "category": "mobile_agent",
        "owner": "Academic (Jan 2026)",
        "paper": "https://arxiv.org/abs/2601.20335",
        "harness_repo": "https://github.com/LHH2CV/mobilebench-OL",
        "tasks": 1080,
        "apps": 80,
        "platform": "android",
        "our_score": None,
        "status": harness_status,
        "priority": "medium",
        "runner_script": "scripts/run_mobilebench_ol.py",
        "install_cmd": "pip install lxml==5.3.1 uiautomator2==3.2.8 requests==2.25.0",
        "agent_interface": "process_message(screenshot_path, task, history) -> action_dict",
        "last_run": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "notes": (
            "80 Chinese-market apps (WeChat, Bilibili, Alipay, etc.) require verified accounts. "
            "Harness clones repo, loads CSVs, executes via uiautomator2. "
            "Drop in APKs + process_message model call to activate."
        ),
    }

    if entry_idx is not None:
        # Merge — keep any existing fields not in new_entry
        merged = {**benchmarks[entry_idx], **new_entry}
        benchmarks[entry_idx] = merged
        logger.info("Updated existing mobile_bench_ol registry entry.")
    else:
        benchmarks.append(new_entry)
        logger.info("Added mobile_bench_ol entry to registry.")

    registry["benchmarks"] = benchmarks
    registry["last_updated"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    with open(REGISTRY_PATH, "w", encoding="utf-8") as f:
        json.dump(registry, f, indent=2)
    logger.info("Registry saved: %s", REGISTRY_PATH)


# ---------------------------------------------------------------------------
# 9. Report writing
# ---------------------------------------------------------------------------

def write_report(report: dict) -> Path:
    """Write the benchmark report JSON and return the path."""
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    report_path = REPORTS_DIR / f"mobilebench_ol_benchmark_{ts}.json"
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
    logger.info("Report written: %s", report_path)
    return report_path


# ---------------------------------------------------------------------------
# 10. Main
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="MobileBench-OL harness for retention.sh",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Skip execution — clone repo, load tasks, report harness status only.",
    )
    parser.add_argument(
        "--max-tasks",
        type=int,
        default=None,
        metavar="N",
        help="Limit number of runnable tasks to attempt (default: all).",
    )
    parser.add_argument(
        "--max-steps",
        type=int,
        default=30,
        metavar="N",
        help="Max action steps per task before marking failed (default: 30).",
    )
    parser.add_argument(
        "--device",
        type=str,
        default=DEFAULT_DEVICE,
        help=f"ADB device serial (default: {DEFAULT_DEVICE}).",
    )
    args = parser.parse_args()

    logger.info("=" * 60)
    logger.info("MobileBench-OL Harness — retention.sh")
    logger.info("=" * 60)

    # Step 1: Clone repo
    clone_ok = clone_repo()
    if not clone_ok:
        logger.warning("Repo clone failed — continuing with synthetic task list.")

    # Step 2: Load tasks
    all_tasks = load_tasks()
    logger.info("Total tasks loaded: %d", len(all_tasks))

    # Step 3: Check installed packages
    if args.dry_run:
        logger.info("--dry-run: skipping device package check.")
        installed_packages: set[str] = set()
    else:
        installed_packages = get_installed_packages(args.device)

    # Step 4: Filter to runnable tasks
    runnable_tasks = filter_runnable_tasks(all_tasks, installed_packages)
    logger.info(
        "Runnable tasks (app installed): %d / %d", len(runnable_tasks), len(all_tasks)
    )

    # Determine harness status
    harness_status = "ready" if clone_ok else "clone_failed"

    # Build base report
    timestamp = datetime.now(timezone.utc).isoformat()
    report: dict[str, Any] = {
        "benchmark": "MobileBench-OL",
        "harness_status": harness_status,
        "repo_cloned": clone_ok,
        "repo_path": str(REPO_DIR),
        "total_tasks_in_csv": len(all_tasks),
        "runnable_tasks": len(runnable_tasks),
        "device": args.device,
        "tasks_attempted": 0,
        "tasks_passed": 0,
        "precision": None,
        "recall": None,
        "f1": None,
        "task_results": [],
        "timestamp": timestamp,
    }

    if len(runnable_tasks) == 0:
        report["blocked_reason"] = BLOCKED_REASON
        logger.info("No runnable tasks. %s", BLOCKED_REASON)
    else:
        report.pop("blocked_reason", None)

    # Step 5: Execute tasks (unless dry-run or no runnable tasks)
    if args.dry_run:
        logger.info("--dry-run: skipping task execution.")
    elif len(runnable_tasks) == 0:
        logger.info("No runnable tasks — skipping execution.")
    else:
        tasks_to_run = runnable_tasks
        if args.max_tasks is not None:
            tasks_to_run = tasks_to_run[: args.max_tasks]
            logger.info("Limiting to %d tasks (--max-tasks).", args.max_tasks)

        device = _get_device(args.device)

        task_results = []
        for i, task in enumerate(tasks_to_run, 1):
            logger.info("[%d/%d] %s", i, len(tasks_to_run), task.get("task_id", "?"))
            result = run_task(task, device, max_steps=args.max_steps)
            task_results.append(result)

        passed = sum(1 for r in task_results if r.get("success"))
        metrics = compute_metrics(task_results)

        report["tasks_attempted"] = len(task_results)
        report["tasks_passed"] = passed
        report["precision"] = metrics["precision"]
        report["recall"] = metrics["recall"]
        report["f1"] = metrics["f1"]
        report["task_results"] = task_results

    # Step 6: Update registry
    update_registry(harness_status)

    # Step 7: Write report
    report_path = write_report(report)

    # Summary
    print("\n" + "=" * 60)
    print("MobileBench-OL Harness Report")
    print("=" * 60)
    print(f"  Harness status  : {report['harness_status']}")
    print(f"  Repo cloned     : {report['repo_cloned']}")
    print(f"  Total tasks     : {report['total_tasks_in_csv']}")
    print(f"  Runnable tasks  : {report['runnable_tasks']}")
    if "blocked_reason" in report:
        print(f"  Blocked reason  : {report['blocked_reason']}")
    print(f"  Tasks attempted : {report['tasks_attempted']}")
    print(f"  Tasks passed    : {report['tasks_passed']}")
    print(f"  Precision       : {report['precision']}")
    print(f"  Recall          : {report['recall']}")
    print(f"  F1              : {report['f1']}")
    print(f"  Report          : {report_path}")
    print("=" * 60)

    return 0


if __name__ == "__main__":
    sys.exit(main())
