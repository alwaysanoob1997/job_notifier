"""Abstract LLM provider interface used by job match scoring."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, ClassVar


class LlmProvider(ABC):
    """One way of talking to an OpenAI-compatible chat completions endpoint.

    Implementations should be cheap to construct (settings are read at call time, not in
    ``__init__``); ``get_active_provider()`` may build a fresh instance each call.
    """

    id: ClassVar[str]
    display_name: ClassVar[str]

    @abstractmethod
    def is_configured(self) -> bool:
        """True when this provider has the minimum settings needed to score a job."""

    @abstractmethod
    def model(self) -> str:
        """Currently selected model id (may be empty when not configured)."""

    @abstractmethod
    def available_models(self) -> tuple[list[str], str | None]:
        """``(model_ids, error)``. ``([], None)`` means "we don't enumerate models"."""

    @abstractmethod
    def chat_completion(
        self,
        messages: list[dict[str, str]],
        *,
        response_format: dict[str, Any],
        temperature: float = 0.2,
    ) -> str:
        """Send a chat completion request and return the assistant message content."""

    def before_inference(self) -> None:
        """Optional setup before the first chat completion (e.g. start a local server)."""

    def after_inference(self, *, had_successful_response: bool) -> None:
        """Optional teardown after scoring. ``had_successful_response`` mirrors current LM Studio
        behaviour: only run teardown when at least one HTTP call returned a usable response, so
        connection failures don't stop a server the operator is debugging."""

    def status_summary(self) -> dict[str, Any]:
        """JSON-serialisable summary for ``GET /api/llm/status``.

        Subclasses may add provider-specific fields (e.g. CLI presence, list errors).
        """
        models, list_error = self.available_models()
        return {
            "id": self.id,
            "display_name": self.display_name,
            "is_configured": self.is_configured(),
            "model": self.model(),
            "models": models,
            "list_error": list_error,
        }
