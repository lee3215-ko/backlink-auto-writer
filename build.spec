# PyInstaller — 백링크 자동 글쓰기 (onedir)
import os

import certifi
from PyInstaller.utils.hooks import collect_all

block_cipher = None
SPEC_DIR = os.path.dirname(os.path.abspath(SPEC))
ICON_FILE = os.path.join(SPEC_DIR, "assets", "icon.ico")

datas = [(ICON_FILE, "assets")] if os.path.isfile(ICON_FILE) else []
datas += [(certifi.where(), "certifi")]
binaries = []
hiddenimports = [
    "tkinter",
    "tkinter.ttk",
    "playwright",
    "playwright.sync_api",
    "faker",
    "ddddocr",
    "cv2",
    "onnxruntime",
    "PIL",
    "numpy",
    "certifi",
    "app_paths",
    "startup_update",
    "update_splash",
]

for pkg in ("ddddocr",):
    try:
        tmp = collect_all(pkg)
        datas += tmp[0]
        binaries += tmp[1]
        hiddenimports += tmp[2]
    except Exception:
        pass

a = Analysis(
    ["main.py"],
    pathex=[SPEC_DIR],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["matplotlib", "pandas", "scipy", "IPython", "pytest"],
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
    name="BacklinkWriter",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=ICON_FILE if os.path.isfile(ICON_FILE) else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="BacklinkWriter",
)
