"""Custom OpenAI-compatible endpoint (e.g. self-hosted llama.cpp / vLLM / Ollama-OpenAI shim)."""

from __future__ import annotations

import logging
from typing import Any, ClassVar

import httpx

from app.config import custom_llm_api_key
from app.llm.base import CancelCheck, LlmProvider
from app.llm_prefs import PROVIDER_CUSTOM, get_provider_block

_logger = logging.getLogger(__name__)

_HTTP_TIMEOUT = httpx.Timeout(300.0, connect=30.0)


class CustomProvider(LlmProvider):
    id: ClassVar[str] = PROVIDER_CUSTOM
    display_name: ClassVar[str] = "Custom endpoint"

    def base_url(self) -> str:
        return get_provider_block(PROVIDER_CUSTOM).get("base_url", "").strip().rstrip("/")

    def model(self) -> str:
        return get_provider_block(PROVIDER_CUSTOM).get("model", "").strip()

    def is_configured(self) -> bool:
        return bool(self.base_url()) and bool(self.model())

    def available_models(self) -> tuple[list[str], str | None]:
        # We deliberately don't probe arbitrary user endpoints; the user types the model id.
        return [], None

    def status_summary(self) -> dict[str, Any]:
        base = super().status_summary()
        base["base_url"] = self.base_url()
        base["api_key_set"] = bool(custom_llm_api_key())
        return base

    def chat_completion(
        self,
        messages: list[dict[str, str]],
        *,
        response_format: dict[str, Any],
        temperature: float = 0.2,
        cancel_check: CancelCheck | None = None,
    ) -> str:
        # ``cancel_check`` is accepted for interface parity. This provider has no
        # internal retry loop, so caller-side cancellation between jobs is enough.
        del cancel_check  # noqa: F841 - declared for interface parity
        url_base = self.base_url()
        if not url_base:
            raise RuntimeError("Custom endpoint base URL is not set")
        model = self.model()
        if not model:
            raise RuntimeError("Custom endpoint model is not set")
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "response_format": response_format,
        }
        headers: dict[str, str] = {}
        api_key = custom_llm_api_key()
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        with httpx.Client(timeout=_HTTP_TIMEOUT) as client:
            r = client.post(
                f"{url_base}/chat/completions",
                json=payload,
                headers=headers,
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
