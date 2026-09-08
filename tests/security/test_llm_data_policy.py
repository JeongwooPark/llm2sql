from __future__ import annotations

from txt2sql.config import Settings
from txt2sql.security.llm_policy import allow_sample_values_for_llm, ollama_is_remote


def test_remote_ollama_blocks_samples_by_default() -> None:
    settings = Settings(
        database_url="postgresql://x:x@localhost/x",
        ollama_host="http://10.0.0.5:11434",
        include_sample_values=True,
        allow_remote_ollama_samples=False,
    )
    assert ollama_is_remote(settings.ollama_host)
    assert allow_sample_values_for_llm(settings) is False


def test_loopback_ollama_allows_samples() -> None:
    settings = Settings(
        database_url="postgresql://x:x@localhost/x",
        ollama_host="http://127.0.0.1:11434",
        include_sample_values=True,
    )
    assert allow_sample_values_for_llm(settings) is True


def test_remote_opt_in() -> None:
    settings = Settings(
        database_url="postgresql://x:x@localhost/x",
        ollama_host="http://ollama.internal:11434",
        include_sample_values=True,
        allow_remote_ollama_samples=True,
    )
    assert allow_sample_values_for_llm(settings) is True
