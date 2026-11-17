# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec file for building the standalone News Archiver Windows app.
Build on Windows with:
    pyinstaller --clean --noconfirm news_archiver.spec
"""

import os
from pathlib import Path
from PyInstaller.utils.hooks import collect_all, collect_data_files

project_root = Path(SPECPATH).resolve()

datas = [
    (str(project_root / "web"), "web"),
]
binaries = []
hiddenimports = [
    "archiver",
    "app_server",
    "uvicorn.logging",
    "uvicorn.loops",
    "uvicorn.loops.auto",
    "uvicorn.loops.asyncio",
    "uvicorn.protocols",
    "uvicorn.protocols.http",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.websockets",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan",
    "uvicorn.lifespan.on",
]

datas += collect_data_files("playwright")

for pkg in ("webview", "pythonnet", "clr_loader"):
    try:
        pkg_datas, pkg_bins, pkg_hidden = collect_all(pkg)
        datas += pkg_datas
        binaries += pkg_bins
        hiddenimports += pkg_hidden
    except Exception:
        pass

# Optional: bundle pre-downloaded Chromium browsers into dist/NewsArchiver/ms-playwright
# Set NEWS_ARCHIVER_PLAYWRIGHT_DIR to a folder containing chromium-* (e.g. %LOCALAPPDATA%\ms-playwright)
bundled_pw_dir = os.environ.get("NEWS_ARCHIVER_PLAYWRIGHT_DIR", "").strip()
if bundled_pw_dir and Path(bundled_pw_dir).is_dir():
    datas.append((bundled_pw_dir, "ms-playwright"))

a = Analysis(
    ["desktop_app.py"],
    pathex=[str(project_root)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="NewsArchiver",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,  # Windowed GUI app (no black console window on Windows)
    disable_windowed_traceback=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="NewsArchiver",
)
