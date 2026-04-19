import logging
import os
import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent


def _default_env_file_path() -> Path:
    """Development: repo-root `.env`. Frozen (PyInstaller): optional `~/LinkedInJobs/.env`."""
    if getattr(sys, "frozen", False):
        return Path.home() / "LinkedInJobs" / ".env"
    return _REPO_ROOT / ".env"


_ENV_FILE = _default_env_file_path()

_logger = logging.getLogger(__name__)

_lmstudio_wsl_url_logged = False


def dotenv_file_path() -> Path:
    """Absolute path to the dotenv file loaded at import (may not exist)."""
    return _ENV_FILE


def _ensure_user_env_file_exists() -> None:
    """Create a minimal `.env` with Settings-managed keys when the file is missing."""
    if _ENV_FILE.is_file():
        return
    try:
        from app.env_user_settings import write_default_env_file_if_missing
    except ImportError:
        return
    try:
        write_default_env_file_if_missing(_ENV_FILE)
    except (OSError, PermissionError) as e:
        _logger.warning("Could not create default .env at %s: %s", _ENV_FILE, e)


def _load_repo_env() -> None:
    if not _ENV_FILE.is_file():
        return
    try:
        from dotenv import load_dotenv
    except ImportError:
        _logger.warning(
            "Dotenv file exists at %s but python-dotenv is not installed; "
            "pip install python-dotenv or export variables in your shell.",
            _ENV_FILE,
        )
        return
    load_dotenv(_ENV_FILE)


_ensure_user_env_file_exists()
_load_repo_env()


def linkedin_jobs_dir() -> Path:
    return Path.home() / "LinkedInJobs"


def database_url() -> str:
    override = os.environ.get("LINKEDIN_JOBS_DB_URL")
    if override:
        return override
    path = linkedin_jobs_dir() / "jobs.db"
    return f"sqlite:///{path}"


def ensure_data_dir() -> Path:
    d = linkedin_jobs_dir()
    d.mkdir(parents=True, exist_ok=True)
    return d


def schedule_status_tzinfo():
    """IANA zone for cron triggers, missed-slot reconciliation, and datetime display in templates.

    Set LINKEDIN_SCHEDULE_STATUS_TZ (e.g. Asia/Kolkata) when the OS reports UTC to Python but you
    want wall-clock schedules elsewhere (typical on WSL). If unset, uses tzlocal.get_localzone()
    (respects TZ when the platform does).
    """
    raw = os.environ.get("LINKEDIN_SCHEDULE_STATUS_TZ", "").strip()
    if raw:
        try:
            from zoneinfo import ZoneInfo

            return ZoneInfo(raw)
        except Exception as e:
            _logger.warning(
                "Invalid LINKEDIN_SCHEDULE_STATUS_TZ=%r (%s); falling back to system local zone.",
                raw,
                e,
            )
    from tzlocal import get_localzone

    return get_localzone()


def llm_scores_database_url() -> str:
    """SQLite URL for job LLM scores (separate file from main jobs.db)."""
    override = os.environ.get("LINKEDIN_LLM_SCORES_DB_URL", "").strip()
    if override:
        return override
    path = linkedin_jobs_dir() / "job_llm_scores.db"
    return f"sqlite:///{path}"


def _is_wsl() -> bool:
    """True when Python is running under WSL (Linux kernel reporting Microsoft)."""
    try:
        with open("/proc/version", encoding="utf-8") as f:
            return "microsoft" in f.read().lower()
    except OSError:
        return False


def _wsl2_windows_host_from_resolv() -> str | None:
    """WSL2: first nameserver in /etc/resolv.conf is typically the Windows host IP."""
    try:
        with open("/etc/resolv.conf", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if line.lower().startswith("nameserver "):
                    parts = line.split()
                    if len(parts) >= 2:
                        host = parts[1].strip()
                        if host and host not in ("127.0.0.1", "::1"):
                            return host
    except OSError:
        return None
    return None


def _wsl2_windows_host_from_default_route() -> str | None:
    """WSL2: ``default via <ip>`` is usually the Windows host (matches LM Studio's logged bind IP).

    ``/etc/resolv.conf`` nameserver can differ (e.g. ``10.255.255.254``), which breaks HTTP to
    Windows LM Studio; prefer this route when ``ip`` is available.
    """
    try:
        r = subprocess.run(
            ["ip", "route", "show", "default"],
            capture_output=True,
            text=True,
            timeout=5,
            shell=False,
        )
        if r.returncode != 0 or not (r.stdout or "").strip():
            return None
        for line in r.stdout.strip().splitlines():
            parts = line.split()
            if "default" not in parts or "via" not in parts:
                continue
            vi = parts.index("via")
            if vi + 1 < len(parts):
                gw = parts[vi + 1].strip()
                if gw and gw not in ("127.0.0.1", "::1"):
                    return gw
    except (FileNotFoundError, subprocess.TimeoutExpired, ValueError):
        return None
    return None


def lmstudio_http_port() -> int:
    """TCP port for LM Studio HTTP (same as ``lms server start --port`` when set)."""
    p = lms_server_start_port()
    if p is not None:
        return p
    raw = os.environ.get("LINKEDIN_LMSTUDIO_PORT", "").strip()
    if raw:
        try:
            n = int(raw)
            if 0 < n <= 65535:
                return n
        except ValueError:
            pass
    return 1234


def _normalize_windows_host_env(raw: str) -> str:
    """Allow ``172.x.x.x`` or accidental ``http://172.x.x.x:1234`` in LINKEDIN_LMSTUDIO_WINDOWS_HOST."""
    h = raw.strip()
    for prefix in ("http://", "https://"):
        if h.lower().startswith(prefix):
            h = h[len(prefix) :]
    h = h.split("/")[0]
    if ":" in h:
        h = h.split(":")[0]
    return h.strip()


def lmstudio_base_url() -> str:
    """OpenAI-compatible API root (LM Studio default).

    Under WSL with LM Studio on **Windows**, ``127.0.0.1`` is Linux-only. When
    ``LINKEDIN_LMSTUDIO_BASE_URL`` is unset we pick a Windows-reachable host in order:

    1. ``LINKEDIN_LMSTUDIO_WINDOWS_HOST`` (copy from LM Studio server log if needed)
    2. Default gateway from ``ip route show default`` (usually matches the log, e.g. ``172.20.x.1``)
    3. First ``nameserver`` in ``/etc/resolv.conf`` (can be wrong vs. LM Studio's bind address)

    Port comes from ``LINKEDIN_LMS_SERVER_PORT``, ``LINKEDIN_LMSTUDIO_PORT``, or ``1234``.
    Set ``LINKEDIN_LMSTUDIO_WSL_AUTODISCOVER=0`` for ``127.0.0.1`` (LM Studio inside Linux on WSL).
    """
    global _lmstudio_wsl_url_logged
    raw = os.environ.get("LINKEDIN_LMSTUDIO_BASE_URL", "").strip().rstrip("/")
    if raw:
        return raw
    port = lmstudio_http_port()
    autodiscover = os.environ.get("LINKEDIN_LMSTUDIO_WSL_AUTODISCOVER", "1").strip().lower()
    if autodiscover in ("0", "false", "no", "off"):
        return f"http://127.0.0.1:{port}/v1"
    if _is_wsl():
        win_host_raw = os.environ.get("LINKEDIN_LMSTUDIO_WINDOWS_HOST", "").strip()
        if win_host_raw:
            wh = _normalize_windows_host_env(win_host_raw)
        else:
            wh = _wsl2_windows_host_from_default_route() or _wsl2_windows_host_from_resolv()
        if wh:
            url = f"http://{wh}:{port}/v1"
            if not _lmstudio_wsl_url_logged:
                _logger.info(
                    "LM Studio base URL (WSL → Windows): %s "
                    "(override with LINKEDIN_LMSTUDIO_BASE_URL or LINKEDIN_LMSTUDIO_WINDOWS_HOST)",
                    url,
                )
                _lmstudio_wsl_url_logged = True
            return url
    return f"http://127.0.0.1:{port}/v1"


_DEFAULT_LMSTUDIO_MODEL = "google/gemma-4-e4b"


def lmstudio_env_overrides_model() -> bool:
    """True when ``LINKEDIN_LMSTUDIO_MODEL`` is set (overrides prefs file)."""
    return bool(os.environ.get("LINKEDIN_LMSTUDIO_MODEL", "").strip())


def lmstudio_model() -> str:
    """Model id for chat completions and `lms unload` (must match loaded model).

    Precedence: ``LINKEDIN_LMSTUDIO_MODEL`` env, then ``~/LinkedInJobs/lmstudio_prefs.json``,
    then default ``google/gemma-4-e4b``.
    """
    raw = os.environ.get("LINKEDIN_LMSTUDIO_MODEL", "").strip()
    if raw:
        return raw
    from app.lmstudio_prefs import get_preferred_model_id

    pref = get_preferred_model_id()
    if pref:
        return pref
    return _DEFAULT_LMSTUDIO_MODEL


def lms_cli_executable() -> str:
    """LM Studio CLI binary name or path.

    Set ``LINKEDIN_LMS_CLI`` in ``.env`` (e.g. ``lms.exe`` on WSL with Windows LM Studio) so
    unload/server-stop subprocess calls resolve. Default is ``lms`` when the variable is unset.
    """
    return os.environ.get("LINKEDIN_LMS_CLI", "lms").strip() or "lms"


def lms_auto_shutdown_enabled() -> bool:
    """After scoring, run `lms unload` and `lms server stop` when inference was attempted.

    Set LINKEDIN_LMS_AUTO_SHUTDOWN=0 or false to keep LM Studio running across runs.
    """
    raw = os.environ.get("LINKEDIN_LMS_AUTO_SHUTDOWN", "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def lms_auto_start_server_enabled() -> bool:
    """Before scoring, run ``lms server start --bind …`` so the API listens for WSL/LAN clients.

    Set ``LINKEDIN_LMS_AUTO_START_SERVER=1`` (e.g. in ``.env``) when LM Studio should be started
    from this app. Default off so operators can start the server manually.
    """
    raw = os.environ.get("LINKEDIN_LMS_AUTO_START_SERVER", "0").strip().lower()
    return raw in ("1", "true", "yes", "on")


def lms_server_bind_address() -> str:
    """Network address for ``lms server start --bind`` (``0.0.0.0`` accepts LAN/WSL to Windows)."""
    raw = os.environ.get("LINKEDIN_LMS_SERVER_BIND", "").strip()
    if raw:
        return raw
    return "0.0.0.0"


def lms_server_start_port() -> int | None:
    """If set, passed as ``lms server start --port N``. Otherwise LM Studio uses default/last port."""
    raw = os.environ.get("LINKEDIN_LMS_SERVER_PORT", "").strip()
    if not raw:
        return None
    try:
        p = int(raw)
    except ValueError:
        return None
    if not (0 < p <= 65535):
        return None
    return p


def lms_server_start_wait_seconds() -> float:
    """Seconds to wait after ``lms server start`` before HTTP scoring (server warm-up)."""
    raw = os.environ.get("LINKEDIN_LMS_SERVER_START_WAIT_SEC", "2")
    try:
        v = float(raw.strip())
    except ValueError:
        return 2.0
    return max(0.0, min(60.0, v))


def schedule_catchup_min_gap_before_next_slot_seconds() -> int:
    """Minimum seconds until the next slot before a missed-slot catch-up may start (default 30m).

    Set LINKEDIN_SCHEDULE_CATCHUP_MIN_GAP_SEC=0 in development to allow catch-up even when
    the next scheduled time is soon; omit or keep default in production.
    """
    raw = os.environ.get("LINKEDIN_SCHEDULE_CATCHUP_MIN_GAP_SEC", "1800")
    try:
        v = int(raw.strip())
    except ValueError:
        return 1800
    return max(0, v)


def scrape_job_limit() -> int:
    """Maximum jobs to fetch per scrape query (``QueryOptions.limit``). Default 100.

    Set ``LINKEDIN_SCRAPE_JOB_LIMIT`` in ``.env`` (e.g. ``2``) to shorten development runs;
    omit or raise the value for production-sized scrapes.
    """
    raw = os.environ.get("LINKEDIN_SCRAPE_JOB_LIMIT", "100")
    try:
        v = int(raw.strip())
    except ValueError:
        return 100
    return max(1, v)


def smtp_host() -> str:
    return os.environ.get("LINKEDIN_SMTP_HOST", "").strip()


def smtp_port() -> int:
    raw = os.environ.get("LINKEDIN_SMTP_PORT", "587").strip()
    try:
        p = int(raw)
    except ValueError:
        return 587
    return max(1, min(65535, p))


def smtp_user() -> str:
    return os.environ.get("LINKEDIN_SMTP_USER", "").strip()


def smtp_password() -> str:
    return os.environ.get("LINKEDIN_SMTP_PASSWORD", "").strip()


def smtp_from_address() -> str:
    raw = os.environ.get("LINKEDIN_SMTP_FROM", "").strip()
    if raw:
        return raw
    u = smtp_user()
    return u


def smtp_use_starttls() -> bool:
    """STARTTLS after connect when not using implicit TLS (port 465).

    Override with LINKEDIN_SMTP_USE_TLS=1/0 when your relay uses a non-standard port.
    """
    raw = os.environ.get("LINKEDIN_SMTP_USE_TLS", "").strip().lower()
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    return smtp_port() != 465


def smtp_force_ssl() -> bool:
    """Use SMTP_SSL (implicit TLS). Default when port is 465; override with LINKEDIN_SMTP_SSL=0."""
    raw = os.environ.get("LINKEDIN_SMTP_SSL", "").strip().lower()
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    return smtp_port() == 465
