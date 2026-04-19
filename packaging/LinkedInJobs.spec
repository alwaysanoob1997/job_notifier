# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for macOS .app (run ``scripts/build_macos_app.sh`` on Darwin)."""

from pathlib import Path

from PyInstaller.utils.hooks import collect_all

# Analysis, PYZ, EXE, COLLECT, BUNDLE are injected by PyInstaller when this spec runs.

block_cipher = None

_REPO_ROOT = Path(SPECPATH).resolve().parent

_datas = [
    (str(_REPO_ROOT / "app" / "templates"), "app/templates"),
    (str(_REPO_ROOT / "app" / "static"), "app/static"),
]
_binaries = []
_hiddenimports = []

for _pkg in (
    "uvicorn",
    "starlette",
    "fastapi",
    "jinja2",
    "sqlalchemy",
    "apscheduler",
    "httpx",
    "anyio",
    "pydantic",
    "pydantic_core",
    "linkedin_jobs_scraper",
    "selenium",
    "webview",
    "tzlocal",
    "tzdata",
    "websockets",
    "httptools",
    "h11",
    "httpcore",
    "certifi",
    "idna",
    "sniffio",
    "dotenv",
    "multipart",
):
    try:
        d, b, h = collect_all(_pkg)
        _datas += d
        _binaries += b
        _hiddenimports += h
    except Exception:
        pass

_hiddenimports += [
    "uvicorn.loops.auto",
    "uvicorn.loops.asyncio",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan.on",
    "pydantic.deprecated.decorator",
    "dns.rdtypes",
    "dns.rdtypes.ANY",
    "dns.rdtypes.IN",
]

a = Analysis(
    [str(_REPO_ROOT / "desktop_main.py")],
    pathex=[str(_REPO_ROOT)],
    binaries=_binaries,
    datas=_datas,
    hiddenimports=_hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="LinkedInJobs",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="LinkedInJobs",
)

app = BUNDLE(
    coll,
    name="LinkedInJobs.app",
    icon=None,
    bundle_identifier="local.linkedinjobs.desktop",
    info_plist={
        "CFBundleName": "LinkedIn Jobs",
        "CFBundleDisplayName": "LinkedIn Jobs",
        "NSHighResolutionCapable": True,
    },
)
