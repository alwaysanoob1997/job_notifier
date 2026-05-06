"""OpenRouter provider (https://openrouter.ai/docs/quickstart).

Free-tier handling
==================

OpenRouter exposes the same chat-completion API for paid and "free" model variants.
Free variants are identified by an id ending in ``:free`` (and/or all-zero ``pricing``
fields in ``GET /api/v1/models``). Their documented limits
(https://openrouter.ai/docs/api-reference/limits) are:

* 20 requests per minute,
* 50 requests per day if the API key has purchased less than 10 credits,
* 1000 requests per day once the key has at least 10 credits.

When the active model is free we acquire a :mod:`app.llm.rate_limit` limiter before
each call and retry once or twice on HTTP 429, honouring ``Retry-After``. Paid
models bypass the limiter entirely.
"""

from __future__ import annotations

import hashlib
import logging
import threading
import time
from typing import Any, ClassVar

import httpx

from app.config import (
    openrouter_api_key,
    openrouter_free_rpd,
    openrouter_free_rpd_default_no_credits,
    openrouter_free_rpd_default_with_credits,
    openrouter_free_rpm,
    openrouter_max_retries,
)
from app.llm.base import LlmProvider, ModelInfo, vendor_from_model_id
from app.llm.rate_limit import CompositeLimiter, SlidingWindowLimiter
from app.llm_prefs import PROVIDER_OPENROUTER, get_provider_block

_logger = logging.getLogger(__name__)

_BASE_URL = "https://openrouter.ai/api/v1"
_HTTP_TIMEOUT = httpx.Timeout(300.0, connect=30.0)
_MODELS_TIMEOUT = httpx.Timeout(20.0, connect=10.0)
_KEY_TIMEOUT = httpx.Timeout(15.0, connect=10.0)
_MODELS_CACHE_TTL_SEC = 600.0
_KEY_CACHE_TTL_SEC = 300.0
_REFERER = "https://github.com/joeyism/linkedin_scraper"
_TITLE = "LinkedIn Jobs Scraper"


class _ModelsCache:
    def __init__(self) -> None:
        self.fetched_at: float = 0.0
        self.models: list[ModelInfo] = []
        self.error: str | None = None


_cache = _ModelsCache()


def _is_cache_fresh() -> bool:
    return _cache.fetched_at > 0 and (time.monotonic() - _cache.fetched_at) < _MODELS_CACHE_TTL_SEC


def invalidate_models_cache() -> None:
    _cache.fetched_at = 0.0
    _cache.models = []
    _cache.error = None


# ---------------------------------------------------------------------------
# /api/v1/key cache (used to pick 50 vs 1000 RPD for free models)
# ---------------------------------------------------------------------------


class _KeyInfoCache:
    def __init__(self) -> None:
        self.fetched_at: float = 0.0
        self.api_key_hash: str = ""
        self.data: dict[str, Any] | None = None
        self.error: str | None = None


_key_cache = _KeyInfoCache()
_key_cache_lock = threading.Lock()


def _hash_api_key(api_key: str) -> str:
    if not api_key:
        return ""
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()[:16]


def fetch_key_info(*, force_refresh: bool = False) -> tuple[dict[str, Any] | None, str | None]:
    """Fetch and cache ``GET /api/v1/key``.

    Returns ``(data, error)`` where ``data`` is the contents of ``response.json()['data']``
    (a dict — see https://openrouter.ai/docs/api-reference/limits for the schema), or
    ``(None, "no api key")`` when the key is unset.
    """
    api_key = openrouter_api_key()
    if not api_key:
        return None, "no api key"
    api_hash = _hash_api_key(api_key)
    now = time.monotonic()
    with _key_cache_lock:
        if (
            not force_refresh
            and _key_cache.api_key_hash == api_hash
            and (now - _key_cache.fetched_at) < _KEY_CACHE_TTL_SEC
        ):
            return _key_cache.data, _key_cache.error
    try:
        with httpx.Client(timeout=_KEY_TIMEOUT) as client:
            r = client.get(
                f"{_BASE_URL}/key",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "HTTP-Referer": _REFERER,
                    "X-OpenRouter-Title": _TITLE,
                },
            )
        r.raise_for_status()
        body = r.json()
    except (httpx.HTTPError, ValueError) as e:
        with _key_cache_lock:
            _key_cache.fetched_at = now
            _key_cache.api_key_hash = api_hash
            _key_cache.data = None
            _key_cache.error = str(e)
        _logger.info("OpenRouter /key probe failed: %s", e)
        return None, str(e)
    data = body.get("data") if isinstance(body, dict) else None
    if not isinstance(data, dict):
        data = None
    with _key_cache_lock:
        _key_cache.fetched_at = now
        _key_cache.api_key_hash = api_hash
        _key_cache.data = data
        _key_cache.error = None
    return data, None


def _resolve_free_daily_cap() -> int:
    """Pick 50/1000/configured RPD using the most recently observed key info."""
    forced = openrouter_free_rpd()
    if forced is not None:
        return forced
    data, _ = fetch_key_info()
    if isinstance(data, dict) and data.get("is_free_tier") is False:
        return openrouter_free_rpd_default_with_credits()
    return openrouter_free_rpd_default_no_credits()


# ---------------------------------------------------------------------------
# Per-(api_key, free) rate limiter cache
# ---------------------------------------------------------------------------


_limiter_cache: dict[tuple[str, bool], CompositeLimiter] = {}
_limiter_cache_lock = threading.Lock()


def _get_free_tier_limiter(api_hash: str) -> CompositeLimiter:
    key = (api_hash, True)
    with _limiter_cache_lock:
        existing = _limiter_cache.get(key)
        if existing is not None:
            return existing
        rpm = openrouter_free_rpm()
        rpd = _resolve_free_daily_cap()
        composite = CompositeLimiter(
            [
                SlidingWindowLimiter(rpm, 60.0, name=f"openrouter-free-rpm({rpm})"),
                SlidingWindowLimiter(rpd, 86400.0, name=f"openrouter-free-rpd({rpd})"),
            ],
            name="openrouter-free",
        )
        _limiter_cache[key] = composite
        return composite


def reset_rate_limiter_cache() -> None:
    """Drop cached limiters (used by tests so each scenario starts clean)."""
    with _limiter_cache_lock:
        _limiter_cache.clear()


def is_free_model_id(model_id: str) -> bool:
    """``True`` for OpenRouter free model variants (suffix ``:free``)."""
    return isinstance(model_id, str) and model_id.strip().endswith(":free")


# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------


class OpenRouterProvider(LlmProvider):
    id: ClassVar[str] = PROVIDER_OPENROUTER
    display_name: ClassVar[str] = "OpenRouter"
    supported_filters: ClassVar[frozenset[str]] = frozenset({"free", "vendor"})

    def model(self) -> str:
        return get_provider_block(PROVIDER_OPENROUTER).get("model", "").strip()

    def is_configured(self) -> bool:
        return bool(openrouter_api_key()) and bool(self.model())

    def available_models(self, *, force_refresh: bool = False) -> tuple[list[str], str | None]:
        infos, err = self.available_models_detailed(force_refresh=force_refresh)
        return [m.id for m in infos], err

    def available_models_detailed(
        self, *, force_refresh: bool = False
    ) -> tuple[list[ModelInfo], str | None]:
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
        infos = _parse_openrouter_models(data)
        _cache.fetched_at = time.monotonic()
        _cache.models = infos
        _cache.error = None
        return list(infos), None

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

        limiter = (
            _get_free_tier_limiter(_hash_api_key(api_key)) if is_free_model_id(model) else None
        )

        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "response_format": response_format,
        }
        max_retries = openrouter_max_retries()
        last_exc: Exception | None = None
        for attempt in range(max_retries + 1):
            if limiter is not None:
                waited = limiter.acquire()
                if waited > 0:
                    _logger.info(
                        "OpenRouter free-tier throttle: waited %.2fs before request",
                        waited,
                    )
            try:
                with httpx.Client(timeout=_HTTP_TIMEOUT) as client:
                    r = client.post(
                        f"{_BASE_URL}/chat/completions",
                        json=payload,
                        headers=self._headers(include_auth=True),
                    )
            except httpx.HTTPError as e:
                last_exc = e
                if attempt >= max_retries:
                    raise
                _logger.warning("OpenRouter request error (attempt %d): %s", attempt + 1, e)
                continue
            if r.status_code == 429:
                retry_after = _retry_after_seconds(r)
                if limiter is not None and retry_after > 0:
                    limiter.note_429(retry_after)
                if attempt >= max_retries:
                    r.raise_for_status()
                _logger.warning(
                    "OpenRouter 429 (attempt %d/%d); backing off %.2fs",
                    attempt + 1,
                    max_retries + 1,
                    retry_after,
                )
                if retry_after > 0 and limiter is None:
                    time.sleep(min(retry_after, 60.0))
                continue
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
        # Loop fell through (only happens on repeated network errors).
        if last_exc is not None:
            raise last_exc
        raise RuntimeError("OpenRouter chat_completion exhausted retries")

    @staticmethod
    def _headers(*, include_auth: bool) -> dict[str, str]:
        h: dict[str, str] = {
            "HTTP-Referer": _REFERER,
            "X-OpenRouter-Title": _TITLE,
        }
        if include_auth:
            h["Authorization"] = f"Bearer {openrouter_api_key()}"
        return h


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _retry_after_seconds(response: httpx.Response) -> float:
    """Parse ``Retry-After`` (seconds or HTTP-date) and ``X-RateLimit-Reset`` headers."""
    h = response.headers
    raw = h.get("retry-after") or h.get("Retry-After")
    if raw:
        s = raw.strip()
        try:
            v = float(s)
            if v >= 0:
                return v
        except ValueError:
            try:
                from email.utils import parsedate_to_datetime

                dt = parsedate_to_datetime(s)
                import datetime as _dt

                now = _dt.datetime.now(_dt.timezone.utc)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=_dt.timezone.utc)
                return max(0.0, (dt - now).total_seconds())
            except (TypeError, ValueError):
                pass
    reset = h.get("x-ratelimit-reset") or h.get("X-RateLimit-Reset")
    if reset:
        try:
            target_ms = float(reset.strip())
            now_ms = time.time() * 1000.0
            wait_s = (target_ms - now_ms) / 1000.0
            if wait_s > 0:
                return wait_s
        except ValueError:
            pass
    return 0.0


def _pricing_is_free(pricing: object) -> bool:
    """OpenRouter pricing fields are strings; a model is free when every numeric field is ``0``."""
    if not isinstance(pricing, dict) or not pricing:
        return False
    for v in pricing.values():
        if v is None:
            continue
        try:
            if float(v) != 0.0:
                return False
        except (TypeError, ValueError):
            return False
    return True


def _parse_openrouter_models(data: object) -> list[ModelInfo]:
    """Extract :class:`ModelInfo` records from ``GET /api/v1/models`` payload.

    Handles three shapes seen in the wild:

    * ``{"data": [<model objects>]}`` (current docs),
    * ``{"models": [...]}`` (older/free clones),
    * a bare list (defensive — some proxies normalise this way).
    """
    items: list[Any]
    if isinstance(data, dict):
        raw = data.get("data") or data.get("models") or []
        items = list(raw) if isinstance(raw, list) else []
    elif isinstance(data, list):
        items = data
    else:
        return []
    out: list[ModelInfo] = []
    seen: set[str] = set()
    for it in items:
        mid: str | None = None
        display: str | None = None
        pricing: object = None
        if isinstance(it, str) and it.strip():
            mid = it.strip()
        elif isinstance(it, dict):
            for key in ("id", "name", "slug"):
                v = it.get(key)
                if isinstance(v, str) and v.strip():
                    mid = v.strip()
                    break
            name = it.get("name")
            if isinstance(name, str) and name.strip():
                display = name.strip()
            pricing = it.get("pricing")
        if not mid or mid in seen:
            continue
        seen.add(mid)
        is_free = is_free_model_id(mid) or _pricing_is_free(pricing)
        out.append(
            ModelInfo(
                id=mid,
                vendor=vendor_from_model_id(mid),
                is_free=is_free,
                display_label=display if display and display != mid else None,
            )
        )
    return out
