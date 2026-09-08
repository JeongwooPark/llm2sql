"""역할별 모델 pin, 공식 벤치 :latest 금지, 질의 트레이스 마스킹."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import unquote

_URL_CRED = re.compile(r"(postgres(?:ql)?://)([^:/@]+):([^@/]+)@", re.IGNORECASE)
_KV_SECRET = re.compile(
    r"(password|passwd|pwd|secret|api[_-]?key|access[_-]?token|refresh[_-]?token|"
    r"session[_-]?secret|database_url|authorization)\s*[=:]\s*([^\s,;]+)",
    re.IGNORECASE,
)
_BEARER = re.compile(r"(?i)(authorization\s*:\s*bearer\s+)(\S+)")
_COOKIE = re.compile(r"(?i)((?:set-)?cookie\s*[:=]\s*)([^;\s]+)")
_QUERY_PASS = re.compile(
    r"(?i)([?&](?:password|passwd|pwd|token|secret|api_key)=)([^&#\s]+)"
)


def is_unpinned_latest(model: str) -> bool:
    name = (model or "").strip().lower()
    return name.endswith(":latest") or name == "latest"


def official_benchmark_allowed(plan_model: str, embed_model: str) -> tuple[bool, str]:
    if is_unpinned_latest(plan_model):
        return False, "plan model uses :latest"
    if is_unpinned_latest(embed_model):
        return False, "embed model uses :latest"
    return True, "ok"


def mask_text(value: str) -> str:
    text = value
    try:
        text = unquote(text)
    except Exception:
        text = value
    text = _URL_CRED.sub(r"\1\2:***@", text)
    text = _BEARER.sub(r"\1***", text)
    text = _COOKIE.sub(r"\1***", text)
    text = _QUERY_PASS.sub(r"\1***", text)
    text = _KV_SECRET.sub(lambda match: f"{match.group(1)}=***", text)
    return text


def mask_value(value: Any) -> Any:
    if isinstance(value, str):
        return mask_text(value)
    if isinstance(value, dict):
        return mask_mapping(value)
    if isinstance(value, list):
        return [mask_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(mask_value(item) for item in value)
    if isinstance(value, set):
        return {mask_value(item) for item in value}
    if isinstance(value, BaseException):
        return mask_text(f"{type(value).__name__}: {value}")
    return value


def mask_mapping(data: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    secret_keys = {
        "password",
        "passwd",
        "secret",
        "database_url",
        "database_url_query",
        "database_url_map",
        "database_url_admin",
        "geoserver_password",
        "api_user_token",
        "api_admin_token",
        "authorization",
        "cookie",
        "set-cookie",
        "access_token",
        "refresh_token",
        "token",
        "session_secret",
    }
    for key, value in data.items():
        lowered = str(key).lower().replace("-", "_")
        if lowered in secret_keys or lowered.endswith("_token") or lowered.endswith("_password"):
            out[key] = "***"
        else:
            out[key] = mask_value(value)
    return out
