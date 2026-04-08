"""Daily data backup — exports Convex state and agent run logs to local JSON.

Runs daily via cron. Backs up:
1. Monitor + digest decisions (last 500 each)
2. Institutional memory
3. Task state (cron configs, daily thread, housekeeping)
4. Eval snapshots
5. Agent run logs (summary + recent detail)

All backups go to data/backups/{YYYY-MM-DD}/ with a manifest.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[3]
_BACKUP_DIR = _REPO_ROOT / "data" / "backups"
_AGENT_RUNS_DIR = _REPO_ROOT / "data" / "agent_runs"


async def run_daily_backup() -> dict[str, Any]:
    """Export all persistent data to local JSON files.

    Returns summary of what was backed up.
    """
    from .convex_client import ConvexClient

    date_str = time.strftime("%Y-%m-%d")
    backup_dir = _BACKUP_DIR / date_str
    backup_dir.mkdir(parents=True, exist_ok=True)

    stats: dict[str, Any] = {
        "date": date_str,
        "backup_dir": str(backup_dir),
        "tables": {},
    }

    convex = ConvexClient()
    try:
        # 1. Monitor decisions
        try:
            monitor = await convex.get_recent_decisions("monitor", limit=500)
            _write_backup(backup_dir / "monitor_decisions.json", monitor)
            stats["tables"]["monitor_decisions"] = len(monitor)
        except Exception as e:
            logger.error("Backup monitor_decisions failed: %s", e)
            stats["tables"]["monitor_decisions"] = f"error: {e}"

        # 2. Digest decisions
        try:
            digest = await convex.get_recent_decisions("digest", limit=500)
            _write_backup(backup_dir / "digest_decisions.json", digest)
            stats["tables"]["digest_decisions"] = len(digest)
        except Exception as e:
            logger.error("Backup digest_decisions failed: %s", e)
            stats["tables"]["digest_decisions"] = f"error: {e}"

        # 3. Task states
        try:
            task_names = [
                "monitor", "digest", "evolve", "standup", "drift",
                "housekeeping", "swarm", "predict",
            ]
            states = {}
            for name in task_names:
                state = await convex.get_task_state(name)
                if state:
                    states[name] = state
            _write_backup(backup_dir / "task_states.json", states)
            stats["tables"]["task_states"] = len(states)
        except Exception as e:
            logger.error("Backup task_states failed: %s", e)

        # 4. Memory search (broad query to get all recent)
        try:
            memory = await convex.search_memory("", limit=200)
            _write_backup(backup_dir / "institutional_memory.json", memory)
            stats["tables"]["institutional_memory"] = len(memory)
        except Exception as e:
            logger.error("Backup institutional_memory failed: %s", e)

    finally:
        await convex.close()

    # 5. Agent run logs — export recent summaries
    try:
        run_summary = _export_agent_runs(days=7)
        _write_backup(backup_dir / "agent_runs_7d.json", run_summary)
        stats["tables"]["agent_runs"] = run_summary.get("total_runs", 0)
    except Exception as e:
        logger.error("Backup agent_runs failed: %s", e)

    # 6. Eval snapshots — copy most recent
    try:
        snapshot_dir = _REPO_ROOT / "data" / "eval_snapshots"
        if snapshot_dir.exists():
            snapshots = sorted(snapshot_dir.glob("*.json"))[-10:]
            snapshot_data = {}
            for s in snapshots:
                snapshot_data[s.name] = json.loads(s.read_text())
            _write_backup(backup_dir / "eval_snapshots.json", snapshot_data)
            stats["tables"]["eval_snapshots"] = len(snapshot_data)
    except Exception as e:
        logger.error("Backup eval_snapshots failed: %s", e)

    # 7. Write manifest
    stats["completed_at"] = time.time()
    _write_backup(backup_dir / "manifest.json", stats)

    logger.info("Daily backup completed: %s", stats)
    return stats


def _write_backup(path: Path, data: Any):
    """Write JSON backup with pretty-printing."""
    path.write_text(json.dumps(data, indent=2, default=str))


def _export_agent_runs(days: int = 7) -> dict[str, Any]:
    """Export agent run summaries from the last N days."""
    import datetime

    cutoff = datetime.datetime.now() - datetime.timedelta(days=days)
    total = 0
    runs_by_agent: dict[str, int] = {}
    errors = 0

    if not _AGENT_RUNS_DIR.exists():
        return {"total_runs": 0, "days": days}

    for date_dir in sorted(_AGENT_RUNS_DIR.iterdir()):
        if not date_dir.is_dir():
            continue
        try:
            dir_date = datetime.datetime.strptime(date_dir.name, "%Y-%m-%d")
            if dir_date < cutoff:
                continue
        except ValueError:
            continue

        for run_file in date_dir.glob("*.json"):
            try:
                run_data = json.loads(run_file.read_text())
                agent = run_data.get("agent", "unknown")
                runs_by_agent[agent] = runs_by_agent.get(agent, 0) + 1
                total += 1
                if run_data.get("error"):
                    errors += 1
            except Exception:
                pass

    return {
        "total_runs": total,
        "days": days,
        "runs_by_agent": runs_by_agent,
        "errors": errors,
        "error_rate": round(errors / max(total, 1), 3),
    }


def get_backup_history(limit: int = 10) -> list[dict]:
    """List recent backups with their manifests."""
    if not _BACKUP_DIR.exists():
        return []
    backups = []
    for d in sorted(_BACKUP_DIR.iterdir(), reverse=True):
        if not d.is_dir():
            continue
        manifest = d / "manifest.json"
        if manifest.exists():
            backups.append(json.loads(manifest.read_text()))
        else:
            backups.append({"date": d.name, "status": "no_manifest"})
        if len(backups) >= limit:
            break
    return backups
