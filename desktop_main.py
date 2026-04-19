"""Desktop shell: FastAPI via embedded uvicorn + native window (pywebview).

Primary target: macOS (.app via PyInstaller). Development: run from repo root
``python desktop_main.py`` with venv activated and ``pip install -r requirements-build.txt``.
"""

from __future__ import annotations

import logging
import socket
import threading
import time
from http.client import HTTPConnection

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def _pick_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _wait_until_up(port: int, timeout_sec: float = 30.0) -> None:
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        try:
            conn = HTTPConnection("127.0.0.1", port, timeout=1.0)
            conn.request("GET", "/")
            resp = conn.getresponse()
            conn.close()
            if resp.status < 600:
                return
        except OSError:
            time.sleep(0.05)
    raise RuntimeError(
        f"Server did not respond on 127.0.0.1:{port} within {timeout_sec}s"
    )


def main() -> None:
    import uvicorn
    import webview

    port = _pick_port()
    config = uvicorn.Config(
        "app.main:app",
        host="127.0.0.1",
        port=port,
        log_level="info",
        lifespan="on",
    )
    server = uvicorn.Server(config)

    thread = threading.Thread(target=server.run, name="uvicorn", daemon=True)
    thread.start()

    try:
        _wait_until_up(port)
    except Exception:
        logger.exception("Uvicorn failed to start")
        server.should_exit = True
        thread.join(timeout=15.0)
        raise

    webview.create_window(
        "LinkedIn Jobs",
        f"http://127.0.0.1:{port}/",
    )
    try:
        webview.start()
    finally:
        logger.info("Stopping uvicorn")
        server.should_exit = True
        thread.join(timeout=30.0)


if __name__ == "__main__":
    main()
