"""OpenRouter provider (https://openrouter.ai/docs/quickstart)."""

from __future__ import annotations

import logging
import time
from typing import Any, ClassVar

import httpx

from app.config import openrouter_api_key
from app.llm.base import LlmProvider
from app.llm_prefs import PROVIDER_OPENROUTER, get_provider_block

_logger = logging.getLogger(__name__)

_BASE_URL = "https://openrouter.ai/api/v1"
_HTTP_TIMEOUT = httpx.Timeout(300.0, connect=30.0)
_MODELS_TIMEOUT = httpx.Timeout(20.0, connect=10.0)
_MODELS_CACHE_TTL_SEC = 600.0
_REFERER = "https://github.com/joeyism/linkedin_scraper"
_TITLE = "LinkedIn Jobs Scraper"


class _ModelsCache:
    def __init__(self) -> None:
        self.fetched_at: float = 0.0
        self.models: list[str] = []
        self.error: str | None = None


_cache = _ModelsCache()


def _is_cache_fresh() -> bool:
    return _cache.fetched_at > 0 and (time.monotonic() - _cache.fetched_at) < _MODELS_CACHE_TTL_SEC


def invalidate_models_cache() -> None:
    _cache.fetched_at = 0.0
    _cache.models = []
    _cache.error = None


class OpenRouterProvider(LlmProvider):
    id: ClassVar[str] = PROVIDER_OPENROUTER
    display_name: ClassVar[str] = "OpenRouter"

    def model(self) -> str:
        return get_provider_block(PROVIDER_OPENROUTER).get("model", "").strip()

    def is_configured(self) -> bool:
        return bool(openrouter_api_key()) and bool(self.model())

    def available_models(self, *, force_refresh: bool = False) -> tuple[list[str], str | None]:
        if not force_refresh and _is_cache_fresh():
            return list(_cache.models), _cache.error
        try:
            with httpx.Client(timeout=_MODELS_TIMEOUT) as client:
                r = client.get(
                    f"{_BASE_URL}/models",
                    headers=self._headers(include_auth=False),
                )
            r.raise_for_status()
            data = r.json()
        except (httpx.HTTPError, ValueError) as e:
            _cache.fetched_at = time.monotonic()
            _cache.error = str(e)
            _cache.models = []
            _logger.warning("OpenRouter /models failed: %s", e)
            return [], _cache.error
        ids = _parse_openrouter_models(data)
        _cache.fetched_at = time.monotonic()
        _cache.models = ids
        _cache.error = None
        return list(ids), None

    def status_summary(self) -> dict[str, Any]:
        base = super().status_summary()
        base["api_key_set"] = bool(openrouter_api_key())
        return base

    def chat_completion(
        self,
        messages: list[dict[str, str]],
        *,
        response_format: dict[str, Any],
        temperature: float = 0.2,
    ) -> str:
        api_key = openrouter_api_key()
        if not api_key:
            raise RuntimeError("OpenRouter API key is not set (APP_OPENROUTER_API_KEY)")
        model = self.model()
        if not model:
            raise RuntimeError("OpenRouter model is not selected")
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "response_format": response_format,
        }
        with httpx.Client(timeout=_HTTP_TIMEOUT) as client:
            r = client.post(
                f"{_BASE_URL}/chat/completions",
                json=payload,
                headers=self._headers(include_auth=True),
            )
        r.raise_for_status()
        body = r.json()
        choices = body.get("choices") or []
        if not choices:
            raise ValueError("no choices in response")
        msg = choices[0].get("message") or {}
        content = msg.get("content")
        if not isinstance(content, str) or not content.strip():
            raise ValueError("empty message content")
        return content.strip()

    @staticmethod
    def _headers(*, include_auth: bool) -> dict[str, str]:
        h: dict[str, str] = {
            "HTTP-Referer": _REFERER,
            "X-OpenRouter-Title": _TITLE,
        }
        if include_auth:
            h["Authorization"] = f"Bearer {openrouter_api_key()}"
        return h


def _parse_openrouter_models(data: object) -> list[str]:
    """Extract model ids from ``GET /api/v1/models`` payload."""
    items: list[Any]
    if isinstance(data, dict):
        raw = data.get("data") or data.get("models") or []
        items = list(raw) if isinstance(raw, list) else []
    elif isinstance(data, list):
        items = data
    else:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for it in items:
        mid: str | None = None
        if isinstance(it, str) and it.strip():
            mid = it.strip()
        elif isinstance(it, dict):
            for key in ("id", "name", "slug"):
                v = it.get(key)
                if isinstance(v, str) and v.strip():
                    mid = v.strip()
                    break
        if mid and mid not in seen:
            seen.add(mid)
            out.append(mid)
    return out
