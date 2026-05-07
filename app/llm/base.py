"""Abstract LLM provider interface used by job match scoring."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, ClassVar


class LlmRequestCancelled(Exception):
    """Raised by a provider's ``chat_completion`` when the optional ``cancel_check``
    callback returns ``True`` between retry attempts.

    The scoring loop catches this and treats it as a cooperative cancellation —
    the job is **not** retried, and the run transitions to the cancelled branch.
    """


CancelCheck = Callable[[], bool]


@dataclass(frozen=True)
class ModelInfo:
    """Provider-agnostic metadata for one selectable model.

    Filterable fields (``vendor``, ``is_free``) may be ``None`` when the provider
    cannot determine the value; the UI only renders a filter control when the
    provider declares the corresponding capability via :attr:`LlmProvider.supported_filters`.
    """

    id: str
    vendor: str | None = None
    is_free: bool | None = None
    display_label: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"id": self.id}
        if self.vendor is not None:
            d["vendor"] = self.vendor
        if self.is_free is not None:
            d["is_free"] = self.is_free
        if self.display_label is not None:
            d["display_label"] = self.display_label
        if self.extra:
            d["extra"] = dict(self.extra)
        return d


def vendor_from_model_id(model_id: str) -> str | None:
    """Best-effort vendor extraction: segment before the first ``/``.

    Reused by every provider whose ids follow the OpenAI-style ``vendor/name`` convention
    (OpenRouter, Hugging Face, LM Studio downloads, ...). Returns ``None`` when the id
    has no namespace separator.
    """
    if not isinstance(model_id, str):
        return None
    s = model_id.strip()
    if "/" not in s:
        return None
    head, _, _ = s.partition("/")
    head = head.strip()
    return head or None


class LlmProvider(ABC):
    """One way of talking to an OpenAI-compatible chat completions endpoint.

    Implementations should be cheap to construct (settings are read at call time, not in
    ``__init__``); ``get_active_provider()`` may build a fresh instance each call.
    """

    id: ClassVar[str]
    display_name: ClassVar[str]

    #: Which model-list filters the provider supplies metadata for. Values:
    #: ``"free"`` (each :class:`ModelInfo` has a non-``None`` ``is_free``) and
    #: ``"vendor"`` (each :class:`ModelInfo` has a non-``None`` ``vendor``).
    #: The UI uses this to decide which filter controls to render.
    supported_filters: ClassVar[frozenset[str]] = frozenset()

    @abstractmethod
    def is_configured(self) -> bool:
        """True when this provider has the minimum settings needed to score a job."""

    @abstractmethod
    def model(self) -> str:
        """Currently selected model id (may be empty when not configured)."""

    @abstractmethod
    def available_models(self) -> tuple[list[str], str | None]:
        """``(model_ids, error)``. ``([], None)`` means "we don't enumerate models"."""

    def available_models_detailed(self) -> tuple[list[ModelInfo], str | None]:
        """Same as :meth:`available_models` but returning :class:`ModelInfo` records.

        Default implementation derives ``vendor`` from the id prefix and leaves
        ``is_free`` as ``None``. Providers that know more (e.g. OpenRouter has pricing)
        should override and populate the relevant fields, and declare the matching
        :attr:`supported_filters`.
        """
        ids, err = self.available_models()
        infos = [ModelInfo(id=mid, vendor=vendor_from_model_id(mid)) for mid in ids]
        return infos, err

    @abstractmethod
    def chat_completion(
        self,
        messages: list[dict[str, str]],
        *,
        response_format: dict[str, Any],
        temperature: float = 0.2,
        cancel_check: CancelCheck | None = None,
    ) -> str:
        """Send a chat completion request and return the assistant message content.

        When ``cancel_check`` is supplied, providers that loop over retries should
        invoke it between attempts and raise :class:`LlmRequestCancelled` as soon as
        it returns ``True``, so a user clicking Stop doesn't have to wait for the
        full retry budget to drain.
        """

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
        infos, list_error = self.available_models_detailed()
        models_detailed = [m.to_dict() for m in infos]
        return {
            "id": self.id,
            "display_name": self.display_name,
            "is_configured": self.is_configured(),
            "model": self.model(),
            "models": [m.id for m in infos],
            "models_detailed": models_detailed,
            "supported_filters": sorted(self.supported_filters),
            "list_error": list_error,
        }
