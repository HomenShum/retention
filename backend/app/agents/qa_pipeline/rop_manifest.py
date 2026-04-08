"""
ROP Manifest Registry — loads, stores, and serves Retained Operation Pattern manifests.

Each ROP is a reusable operation pattern (not just history) that agents can:
  - recognize (via triggers)
  - invoke (via progressive disclosure)
  - partially replay (via suggest_next)
  - audit (via verification checklist)
  - measure (via KPI tracking)

Progressive disclosure layers:
  Layer 0: Skill card — tiny metadata only
  Layer 1: Route skeleton — branches, clusters, stages
  Layer 2: Relevant subpaths — URL/directory groups, checkpoints
  Layer 3: Action suggestion — suggest_next() prefix match
  Layer 4: Audit/divergence — stop, flag, recommend fallback
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

_MANIFEST_DIR = Path(__file__).resolve().parents[3] / "data" / "rop_manifests"
_MANIFEST_DIR.mkdir(parents=True, exist_ok=True)


# ─── Data types ──────────────────────────────────────────────────────────

@dataclass
class ROPManifest:
    """A Retained Operation Pattern manifest."""
    id: str
    name: str
    short_name: str
    category: str
    version: str
    purpose: str
    triggers: list[str]
    surfaces: list[str]
    subagent_roles: dict[str, str]
    retrieval_strategy: dict[str, Any]
    progressive_disclosure: dict[str, Any]
    prefix_signature: list[str]
    suggest_next_policy: dict[str, Any]
    divergence_policy: dict[str, Any]
    outputs: list[str]
    audit: dict[str, Any]
    kpis: dict[str, str] = field(default_factory=dict)

    def card(self) -> dict[str, Any]:
        """Layer 0: Skill card — tiny metadata for agent routing."""
        pd = self.progressive_disclosure.get("layer_0_card", {})
        return {
            "id": self.id,
            "name": self.name,
            "short_name": self.short_name,
            "when_to_use": pd.get("when_to_use", self.purpose),
            "expected_output": pd.get("expected_output", ", ".join(self.outputs)),
            "risk_level": pd.get("risk_level", "medium"),
            "typical_savings": pd.get("typical_savings", "unknown"),
            "triggers": self.triggers,
        }

    def skeleton(self) -> dict[str, Any]:
        """Layer 1: Route skeleton — branches, clusters, stages."""
        return {
            "id": self.id,
            "name": self.name,
            "stages": self.progressive_disclosure.get("layer_1_skeleton", []),
            "clusters": [
                {"id": g["cluster_id"], "description": g["description"], "utility": g.get("utility_score", 0.5)}
                for g in self.retrieval_strategy.get("grouping", [])
            ],
            "surfaces": self.surfaces,
            "subagent_roles": list(self.subagent_roles.keys()),
            "prefix_signature": self.prefix_signature,
        }

    def subpaths(self, cluster_id: str) -> dict[str, Any]:
        """Layer 2: Relevant subpaths for a committed cluster."""
        for g in self.retrieval_strategy.get("grouping", []):
            if g["cluster_id"] == cluster_id:
                return {
                    "cluster_id": cluster_id,
                    "description": g["description"],
                    "utility_score": g.get("utility_score", 0.5),
                    "directories": g.get("directories", []),
                    "suggest_policy": self.suggest_next_policy,
                    "divergence_policy": self.divergence_policy,
                }
        return {"error": f"Cluster '{cluster_id}' not found in {self.id}"}

    def action_policy(self) -> dict[str, Any]:
        """Layer 3: Action suggestion policy for suggest_next()."""
        return {
            "id": self.id,
            "method": self.suggest_next_policy.get("method", "prefix_match"),
            "min_confidence": self.suggest_next_policy.get("min_confidence", 0.65),
            "fallback": self.suggest_next_policy.get("fallback", "agent_reasons_independently"),
        }

    def audit_checklist(self) -> dict[str, Any]:
        """Layer 4: Audit and divergence policy."""
        return {
            "id": self.id,
            "audit_required": self.audit.get("required", True),
            "verify": self.audit.get("verify", []),
            "stop_on": self.divergence_policy.get("stop_on", []),
            "kpis": self.kpis,
        }


# ─── Registry ────────────────────────────────────────────────────────────

class ROPRegistry:
    """Loads and serves ROP manifests from disk."""

    def __init__(self, manifest_dir: Optional[Path] = None):
        self._dir = manifest_dir or _MANIFEST_DIR
        self._manifests: dict[str, ROPManifest] = {}
        self._loaded = False

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        self._load_all()
        self._loaded = True

    def _load_all(self) -> None:
        """Load all manifest JSON files from disk."""
        if not self._dir.exists():
            return
        for f in self._dir.glob("*.json"):
            try:
                data = json.loads(f.read_text())
                manifest = ROPManifest(
                    id=data["id"],
                    name=data["name"],
                    short_name=data.get("short_name", data["id"]),
                    category=data.get("category", "retained_operation_pattern"),
                    version=data.get("version", "1.0.0"),
                    purpose=data.get("purpose", ""),
                    triggers=data.get("triggers", []),
                    surfaces=data.get("surfaces", []),
                    subagent_roles=data.get("subagent_roles", {}),
                    retrieval_strategy=data.get("retrieval_strategy", {}),
                    progressive_disclosure=data.get("progressive_disclosure", {}),
                    prefix_signature=data.get("prefix_signature", []),
                    suggest_next_policy=data.get("suggest_next_policy", {}),
                    divergence_policy=data.get("divergence_policy", {}),
                    outputs=data.get("outputs", []),
                    audit=data.get("audit", {}),
                    kpis=data.get("kpis", {}),
                )
                self._manifests[manifest.id] = manifest
                logger.debug(f"Loaded ROP manifest: {manifest.id}")
            except (json.JSONDecodeError, KeyError) as e:
                logger.warning(f"Failed to load manifest {f.name}: {e}")

    def get(self, rop_id: str) -> Optional[ROPManifest]:
        """Get a manifest by ID."""
        self._ensure_loaded()
        return self._manifests.get(rop_id)

    def list_all(self) -> list[ROPManifest]:
        """List all loaded manifests."""
        self._ensure_loaded()
        return list(self._manifests.values())

    def list_cards(self) -> list[dict[str, Any]]:
        """List Layer 0 cards for all manifests — used for agent routing."""
        self._ensure_loaded()
        return [m.card() for m in self._manifests.values()]

    def match_trigger(self, user_request: str) -> Optional[ROPManifest]:
        """Match a user request against manifest triggers.

        Returns the best-matching manifest, or None.
        """
        self._ensure_loaded()
        request_lower = user_request.lower()

        best_match: Optional[ROPManifest] = None
        best_score = 0.0

        for manifest in self._manifests.values():
            for trigger in manifest.triggers:
                trigger_lower = trigger.lower()
                if trigger_lower in request_lower:
                    # Exact substring match — high score
                    score = len(trigger_lower) / len(request_lower)
                    if score > best_score:
                        best_score = score
                        best_match = manifest

        return best_match

    def save_manifest(self, manifest: ROPManifest) -> None:
        """Save a manifest to disk."""
        data = {
            "id": manifest.id,
            "name": manifest.name,
            "short_name": manifest.short_name,
            "category": manifest.category,
            "version": manifest.version,
            "purpose": manifest.purpose,
            "triggers": manifest.triggers,
            "surfaces": manifest.surfaces,
            "subagent_roles": manifest.subagent_roles,
            "retrieval_strategy": manifest.retrieval_strategy,
            "progressive_disclosure": manifest.progressive_disclosure,
            "prefix_signature": manifest.prefix_signature,
            "suggest_next_policy": manifest.suggest_next_policy,
            "divergence_policy": manifest.divergence_policy,
            "outputs": manifest.outputs,
            "audit": manifest.audit,
            "kpis": manifest.kpis,
        }
        path = self._dir / f"{manifest.id}.json"
        path.write_text(json.dumps(data, indent=2))
        self._manifests[manifest.id] = manifest
        logger.info(f"Saved ROP manifest: {manifest.id}")

    def reload(self) -> None:
        """Force reload all manifests from disk."""
        self._manifests.clear()
        self._loaded = False
        self._ensure_loaded()


# ─── Module-level singleton ──────────────────────────────────────────────

_registry: Optional[ROPRegistry] = None


def get_rop_registry() -> ROPRegistry:
    """Get or create the global ROP registry singleton."""
    global _registry
    if _registry is None:
        _registry = ROPRegistry()
    return _registry
