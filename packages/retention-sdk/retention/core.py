"""Core entry point for retention telemetry.

Usage:
    from retention import track
    track()  # Auto-detects installed providers and patches them

    # Or configure explicitly:
    from retention import configure
    configure(providers=["openai", "anthropic"], scrub=True, log_path="./my.jsonl")
    track()
"""

from typing import Optional

# Module-level configuration
_config = {
    "providers": None,  # None = auto-detect all; list = only these
    "scrub": True,
    "log_path": None,  # None = default ~/.retention/activity.jsonl
    "endpoint": None,  # Future: remote ingestion endpoint
    "patched": set(),  # Track which providers have been patched
}


def configure(
    providers: Optional[list] = None,
    scrub: bool = True,
    log_path: Optional[str] = None,
    endpoint: Optional[str] = None,
):
    """Configure retention before calling track().

    Args:
        providers: List of provider names to patch. None = auto-detect all.
                   Valid: "openai", "anthropic", "langchain", "crewai",
                          "openai_agents", "claude_agent", "generic"
        scrub: Whether to scrub sensitive data from logged events (default True).
        log_path: Override the default JSONL log file path.
        endpoint: Future: remote endpoint for event ingestion.
    """
    _config["providers"] = providers
    _config["scrub"] = scrub
    if log_path is not None:
        _config["log_path"] = log_path
        from retention.storage import set_log_path
        set_log_path(log_path)
    if endpoint is not None:
        _config["endpoint"] = endpoint


# Registry of all available wrappers: name -> module path
_WRAPPER_REGISTRY = {
    "openai": "retention.wrappers.openai_wrapper",
    "anthropic": "retention.wrappers.anthropic_wrapper",
    "langchain": "retention.wrappers.langchain_wrapper",
    "crewai": "retention.wrappers.crewai_wrapper",
    "openai_agents": "retention.wrappers.openai_agents",
    "claude_agent": "retention.wrappers.claude_agent",
}


def track(providers: Optional[list] = None):
    """Auto-detect installed AI providers and patch them for telemetry.

    Each wrapper is optional -- if the provider library is not installed,
    it is silently skipped. Returns a dict of provider -> patched (bool).

    Args:
        providers: Override which providers to patch. None uses configure() setting.

    Returns:
        dict: Mapping of provider name to whether it was successfully patched.
    """
    target_providers = providers or _config["providers"]
    results = {}

    if target_providers is None:
        # Auto-detect: try all wrappers
        target_providers = list(_WRAPPER_REGISTRY.keys())

    for name in target_providers:
        if name in _config["patched"]:
            results[name] = True
            continue

        module_path = _WRAPPER_REGISTRY.get(name)
        if module_path is None:
            results[name] = False
            continue

        try:
            import importlib
            mod = importlib.import_module(module_path)
            success = mod.patch()
            results[name] = bool(success)
            if success:
                _config["patched"].add(name)
        except Exception:
            results[name] = False

    return results
