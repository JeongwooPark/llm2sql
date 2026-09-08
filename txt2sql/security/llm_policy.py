"""LLM으로 보내는 샘플·원문 데이터 정책."""

from __future__ import annotations

from urllib.parse import urlparse

from txt2sql.config import Settings
from txt2sql.security.deploy import is_loopback_host


def ollama_is_remote(host: str) -> bool:
    raw = (host or "").strip()
    if not raw:
        return False
    parsed = urlparse(raw if "://" in raw else f"http://{raw}")
    hostname = (parsed.hostname or "").strip().lower()
    return not is_loopback_host(hostname)


def allow_sample_values_for_llm(settings: Settings) -> bool:
    """INCLUDE_SAMPLE_VALUES may not override remote Ollama denial."""
    if not settings.include_sample_values:
        return False
    if ollama_is_remote(settings.ollama_host) and not settings.allow_remote_ollama_samples:
        return False
    return True
