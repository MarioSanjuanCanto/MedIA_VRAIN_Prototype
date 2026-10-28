"""Shared pytest configuration for Media Core."""

import pytest

@pytest.fixture(autouse=True)
def isolate_llm_cache(tmp_path, monkeypatch):
    from media_core.services import openrouter
    monkeypatch.setattr(openrouter, "CACHE_DIR", tmp_path / "cache")
