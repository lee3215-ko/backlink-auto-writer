# PyInstaller — 백링크 자동 글쓰기 (onedir)
import os

import certifi
from PyInstaller.utils.hooks import collect_all, collect_data_files

block_cipher = None
SPEC_DIR = os.path.dirname(os.path.abspath(SPEC))
ICON_FILE = os.path.join(SPEC_DIR, "assets", "icon.ico")

datas = [(ICON_FILE, "assets")] if os.path.isfile(ICON_FILE) else []
datas += [(certifi.where(), "certifi")]
binaries = []

# 로컬 모듈 — Analysis 누락 방지 (다른 PC에서 ModuleNotFoundError 발생)
LOCAL_MODULES = [
    "ai_assist",
    "app_constants",
    "app_logger",
    "app_paths",
    "app_state",
    "article_builder",
    "batch_jobs",
    "board_auto_search",
    "board_catalog",
    "board_discoverer",
    "board_probe",
    "board_probed",
    "board_search",
    "board_url",
    "board_writer",
    "browser_prefs",
    "browser_session",
    "browser_window",
    "captcha_solver",
    "comment_writer",
    "custom_bbs_comment",
    "editor_content",
    "error_messages",
    "excluded_urls",
    "form_autofill",
    "forum_url",
    "generic_comment",
    "html_mode",
    "link_utils",
    "log_sync",
    "movable_type_comment",
    "page_guard",
    "page_snapshot",
    "phpbb_comment",
    "post_history",
    "sets_panel",
    "startup_update",
    "target_jobs",
    "unsupported_report",
    "update_splash",
    "update_ui",
    "updater",
    "url_analyzer",
    "url_recommend",
    "win_ui",
    "wordpress_comment",
    "zeroboard_writer",
]

hiddenimports = [
    "tkinter",
    "tkinter.ttk",
    "tkinter.messagebox",
    "tkinter.scrolledtext",
    "playwright",
    "playwright.sync_api",
    "faker",
    "ddddocr",
    "cv2",
    "onnxruntime",
    "PIL",
    "numpy",
    "certifi",
    *LOCAL_MODULES,
]

# setuptools jaraco.text — pyi_rth_pkgres 가 Lorem ipsum.txt 를 읽음 (미포함 시 exe 시작 실패)
try:
    datas += collect_data_files("setuptools._vendor.jaraco.text")
except Exception:
    pass
try:
    import setuptools

    _jaraco_txt = os.path.join(
        os.path.dirname(setuptools.__file__),
        "_vendor",
        "jaraco",
        "text",
        "Lorem ipsum.txt",
    )
    if os.path.isfile(_jaraco_txt):
        datas.append((_jaraco_txt, "setuptools/_vendor/jaraco/text"))
except Exception:
    pass

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
    excludes=[
        "matplotlib",
        "pandas",
        "scipy",
        "IPython",
        "pytest",
        "pkg_resources",
    ],
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
