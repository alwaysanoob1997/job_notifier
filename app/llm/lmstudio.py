"""LM Studio provider: ``lms`` CLI for model discovery + LM Studio HTTP for inference."""

from __future__ import annotations

import logging
import subprocess
import time
from typing import Any, ClassVar

import httpx

from app.config import (
    lms_auto_shutdown_enabled,
    lms_auto_start_server_enabled,
    lms_server_bind_address,
    lms_server_start_port,
    lms_server_start_wait_seconds,
    lmstudio_base_url,
    lmstudio_env_overrides_model,
    lmstudio_model,
)
from app.llm.base import LlmProvider
from app.lmstudio_cli import (
    lms_cli_available,
    lms_executable_for_subprocess,
    list_downloaded_models,
)
from app.llm_prefs import PROVIDER_LMSTUDIO, get_preferred_model_id

_logger = logging.getLogger(__name__)

_HTTP_TIMEOUT = httpx.Timeout(300.0, connect=30.0)
_LMS_SUBPROCESS_TIMEOUT = 120


class LmStudioProvider(LlmProvider):
    id: ClassVar[str] = PROVIDER_LMSTUDIO
    display_name: ClassVar[str] = "LM Studio (local)"

    def model(self) -> str:
        return lmstudio_model()

    def is_configured(self) -> bool:
        if not lms_cli_available():
            return False
        if lmstudio_env_overrides_model():
            return True
        return get_preferred_model_id() is not None

    def available_models(self) -> tuple[list[str], str | None]:
        if not lms_cli_available():
            return [], None
        return list_downloaded_models()

    def status_summary(self) -> dict[str, Any]:
        base = super().status_summary()
        base["cli_available"] = lms_cli_available()
        base["env_overrides_model"] = lmstudio_env_overrides_model()
        return base

    def chat_completion(
        self,
        messages: list[dict[str, str]],
        *,
        response_format: dict[str, Any],
        temperature: float = 0.2,
    ) -> str:
        url = f"{lmstudio_base_url().rstrip('/')}/chat/completions"
        payload: dict[str, Any] = {
            "model": self.model(),
            "messages": messages,
            "temperature": temperature,
            "response_format": response_format,
        }
        with httpx.Client(timeout=_HTTP_TIMEOUT) as client:
            r = client.post(
                url,
                json=payload,
                headers={"Authorization": "Bearer lm-studio"},
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

    def before_inference(self) -> None:
        """``lms server start --bind …`` so WSL/LAN can reach the HTTP API (optional via env)."""
        if not lms_auto_start_server_enabled():
            return
        exe = lms_executable_for_subprocess()
        bind = lms_server_bind_address()
        cmd = [exe, "server", "start", "--bind", bind]
        port = lms_server_start_port()
        if port is not None:
            cmd.extend(["--port", str(port)])
        _logger.info("LM Studio: starting server (%s)", " ".join(cmd))
        r = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=_LMS_SUBPROCESS_TIMEOUT,
            shell=False,
        )
        if r.returncode != 0:
            _logger.warning(
                "lms server start failed (code=%s): %s — continuing in case server was already running",
                r.returncode,
                (r.stderr or r.stdout or "").strip()[:500],
            )
        wait = lms_server_start_wait_seconds()
        if wait > 0:
            time.sleep(wait)

    def after_inference(self, *, had_successful_response: bool) -> None:
        """Unload model and stop LM Studio server (best-effort; logs warnings)."""
        if not had_successful_response:
            return
        if not lms_auto_shutdown_enabled():
            return
        model = self.model()
        r1 = self._run_lms(["unload", model])
        if r1.returncode != 0:
            _logger.warning(
                "lms unload %s failed (code=%s): %s",
                model,
                r1.returncode,
                (r1.stderr or r1.stdout or "").strip()[:500],
            )
            r1b = self._run_lms(["unload", "--all"])
            if r1b.returncode != 0:
                _logger.warning(
                    "lms unload --all failed (code=%s): %s",
                    r1b.returncode,
                    (r1b.stderr or r1b.stdout or "").strip()[:500],
                )
        r2 = self._run_lms(["server", "stop"])
        if r2.returncode != 0:
            _logger.warning(
                "lms server stop failed (code=%s): %s",
                r2.returncode,
                (r2.stderr or r2.stdout or "").strip()[:500],
            )

    @staticmethod
    def _run_lms(args: list[str]) -> subprocess.CompletedProcess[str]:
        exe = lms_executable_for_subprocess()
        return subprocess.run(
            [exe, *args],
            capture_output=True,
            text=True,
            timeout=_LMS_SUBPROCESS_TIMEOUT,
            shell=False,
        )
