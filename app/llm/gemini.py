"""Google Gemini provider via the OpenAI-compatible endpoint.

Gemini exposes ``https://generativelanguage.googleapis.com/v1beta/openai/`` which
accepts the OpenAI ``messages`` + ``response_format`` payload we already send to
other providers (https://ai.google.dev/gemini-api/docs/openai). That keeps the
shared :func:`app.services.job_match_scoring._score_one_job` call site identical
and lets us reuse :class:`app.llm.rate_limit.SlidingWindowLimiter` for client-side
self-throttling.

Per-tier limits vary per model (https://ai.google.dev/gemini-api/docs/rate-limits);
the default 60 RPM is conservative enough for the tier-1 / paid tiers and easily
overridden via ``APP_GEMINI_RPM`` for stricter free-tier models.
"""

from __future__ import annotations

import hashlib
import logging
import threading
import time
from typing import Any, ClassVar

import httpx

from app.config import gemini_api_key, gemini_max_retries, gemini_rpm
from app.llm.base import CancelCheck, LlmProvider, LlmRequestCancelled, ModelInfo
from app.llm.rate_limit import SlidingWindowLimiter
from app.llm_prefs import PROVIDER_GEMINI, get_provider_block

_logger = logging.getLogger(__name__)

_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai"
# Healthy chat completions return in 1.5–10s (see post-fix logs). 60s is well above
# the slowest legitimate response we observed but quickly gives up on stalled
# requests. With ``gemini_max_retries=2`` (3 attempts), worst case per call is
# ~3 × 60s = 3 min instead of the previous ~3 × 300s = 15 min.
_HTTP_TIMEOUT = httpx.Timeout(60.0, connect=15.0)
_MODELS_TIMEOUT = httpx.Timeout(20.0, connect=10.0)
_MODELS_CACHE_TTL_SEC = 600.0

_DEFAULT_MODEL = "gemma-4-26b-a4b-it"


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
# Per-api-key rate limiter cache
# ---------------------------------------------------------------------------


_limiter_cache: dict[str, SlidingWindowLimiter] = {}
_limiter_cache_lock = threading.Lock()


def _hash_api_key(api_key: str) -> str:
    if not api_key:
        return ""
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()[:16]


def _get_rpm_limiter(api_hash: str) -> SlidingWindowLimiter:
    with _limiter_cache_lock:
        existing = _limiter_cache.get(api_hash)
        if existing is not None:
            return existing
        rpm = gemini_rpm()
        limiter = SlidingWindowLimiter(rpm, 60.0, name=f"gemini-rpm({rpm})")
        _limiter_cache[api_hash] = limiter
        return limiter


def reset_rate_limiter_cache() -> None:
    """Drop cached limiters (used by tests so each scenario starts clean)."""
    with _limiter_cache_lock:
        _limiter_cache.clear()


# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------


class GeminiProvider(LlmProvider):
    id: ClassVar[str] = PROVIDER_GEMINI
    display_name: ClassVar[str] = "Google Gemini"
    supported_filters: ClassVar[frozenset[str]] = frozenset()

    def model(self) -> str:
        stored = get_provider_block(PROVIDER_GEMINI).get("model", "").strip()
        return stored or _DEFAULT_MODEL

    def is_configured(self) -> bool:
        return bool(gemini_api_key()) and bool(self.model())

    def available_models(self, *, force_refresh: bool = False) -> tuple[list[str], str | None]:
        infos, err = self.available_models_detailed(force_refresh=force_refresh)
        return [m.id for m in infos], err

    def available_models_detailed(
        self, *, force_refresh: bool = False
    ) -> tuple[list[ModelInfo], str | None]:
        if not force_refresh and _is_cache_fresh():
            return list(_cache.models), _cache.error
        api_key = gemini_api_key()
        if not api_key:
            _cache.fetched_at = time.monotonic()
            _cache.error = "no api key"
            _cache.models = []
            return [], _cache.error
        try:
            with httpx.Client(timeout=_MODELS_TIMEOUT) as client:
                r = client.get(
                    f"{_BASE_URL}/models",
                    headers={"Authorization": f"Bearer {api_key}"},
                )
            r.raise_for_status()
            data = r.json()
        except (httpx.HTTPError, ValueError) as e:
            _cache.fetched_at = time.monotonic()
            _cache.error = str(e)
            _cache.models = []
            _logger.warning("Gemini /models failed: %s", e)
            return [], _cache.error
        infos = _parse_gemini_models(data)
        _cache.fetched_at = time.monotonic()
        _cache.models = infos
        _cache.error = None
        return list(infos), None

    def status_summary(self) -> dict[str, Any]:
        base = super().status_summary()
        base["api_key_set"] = bool(gemini_api_key())
        return base

    def chat_completion(
        self,
        messages: list[dict[str, str]],
        *,
        response_format: dict[str, Any],
        temperature: float = 0.2,
        cancel_check: CancelCheck | None = None,
    ) -> str:
        api_key = gemini_api_key()
        if not api_key:
            raise RuntimeError("Gemini API key is not set (APP_GEMINI_API_KEY)")
        model = self.model()
        if not model:
            raise RuntimeError("Gemini model is not selected")

        limiter = _get_rpm_limiter(_hash_api_key(api_key))
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "response_format": response_format,
        }
        max_retries = gemini_max_retries()
        last_exc: Exception | None = None
        for attempt in range(max_retries + 1):
            # Cooperative cancel check: skip the entire attempt (no acquire, no HTTP)
            # when the caller has already requested cancellation. This bounds the
            # worst-case time-to-respond-to-Stop to roughly the duration of a single
            # in-flight HTTP attempt (capped by ``_HTTP_TIMEOUT``).
            if cancel_check is not None and cancel_check():
                raise LlmRequestCancelled("scoring cancelled by user")
            waited = limiter.acquire()
            if waited > 0:
                _logger.info(
                    "Gemini throttle: waited %.2fs before request",
                    waited,
                )
            try:
                with httpx.Client(timeout=_HTTP_TIMEOUT) as client:
                    r = client.post(
                        f"{_BASE_URL}/chat/completions",
                        json=payload,
                        headers={"Authorization": f"Bearer {api_key}"},
                    )
            except httpx.HTTPError as e:
                last_exc = e
                if attempt >= max_retries:
                    raise
                _logger.warning("Gemini request error (attempt %d): %s", attempt + 1, e)
                continue
            if r.status_code == 429:
                retry_after = _retry_after_seconds(r)
                if retry_after > 0:
                    limiter.note_429(retry_after)
                if attempt >= max_retries:
                    r.raise_for_status()
                _logger.warning(
                    "Gemini 429 (attempt %d/%d); backing off %.2fs",
                    attempt + 1,
                    max_retries + 1,
                    retry_after,
                )
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
        if last_exc is not None:
            raise last_exc
        raise RuntimeError("Gemini chat_completion exhausted retries")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _retry_after_seconds(response: httpx.Response) -> float:
    """Parse ``Retry-After`` (seconds or HTTP-date) headers; mirrors OpenRouter behaviour."""
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
    return 0.0


def _normalise_model_id(raw: str) -> str:
    """Strip the ``models/`` prefix Gemini sometimes returns from ``/models``."""
    s = raw.strip()
    if s.startswith("models/"):
        s = s[len("models/") :]
    return s


def _parse_gemini_models(data: object) -> list[ModelInfo]:
    """Extract :class:`ModelInfo` records from ``GET /v1beta/openai/models`` payload.

    The OpenAI-compat shape is ``{"object": "list", "data": [{"id": "...", ...}, ...]}``.
    A bare list and the native ``{"models": [...]}`` shape are accepted defensively.
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
        if isinstance(it, str) and it.strip():
            mid = _normalise_model_id(it)
        elif isinstance(it, dict):
            for key in ("id", "name"):
                v = it.get(key)
                if isinstance(v, str) and v.strip():
                    mid = _normalise_model_id(v)
                    break
        if not mid or mid in seen:
            continue
        seen.add(mid)
        out.append(ModelInfo(id=mid))
    return out
