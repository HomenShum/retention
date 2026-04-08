"""Figma ingestion module.

This package provides:
- URL parsing helpers (extract file_key/node_id)
- A small httpx-based REST client for Figma
- A service layer that supports progressive disclosure (metadata → components/styles → full)
"""
