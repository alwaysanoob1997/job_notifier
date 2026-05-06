"""Resolve which :class:`LlmProvider` is currently active."""

from __future__ import annotations

import os

from app.llm.base import LlmProvider
from app.llm.custom import CustomProvider
from app.llm.gemini import GeminiProvider
from app.llm.lmstudio import LmStudioProvider
from app.llm_prefs import (
    PROVIDER_CUSTOM,
    PROVIDER_GEMINI,
    PROVIDER_LMSTUDIO,
    get_active_provider_id,
)

PROVIDER_IDS: tuple[str, ...] = (PROVIDER_LMSTUDIO, PROVIDER_GEMINI, PROVIDER_CUSTOM)


def get_provider(provider_id: str) -> LlmProvider:
    if provider_id == PROVIDER_LMSTUDIO:
        return LmStudioProvider()
    if provider_id == PROVIDER_GEMINI:
        return GeminiProvider()
    if provider_id == PROVIDER_CUSTOM:
        return CustomProvider()
    raise ValueError(f"unknown provider id: {provider_id!r}")


def all_providers() -> list[LlmProvider]:
    return [get_provider(pid) for pid in PROVIDER_IDS]


def active_provider_id() -> str:
    """Selected provider id; ``APP_LLM_PROVIDER`` env wins over the prefs file."""
    env = (os.environ.get("APP_LLM_PROVIDER") or "").strip().lower()
    if env in PROVIDER_IDS:
        return env
    pid = get_active_provider_id()
    if pid in PROVIDER_IDS:
        return pid
    return PROVIDER_LMSTUDIO


def get_active_provider() -> LlmProvider:
    return get_provider(active_provider_id())
