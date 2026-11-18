# PyInstaller build definition for the Atlas Windows desktop app.
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules, copy_metadata


ROOT = Path(SPECPATH).resolve().parent.parent
datas = [(str(ROOT / "web_panel"), "web_panel")]
binaries = []
hiddenimports = []

for package in (
    "cv2",
    "mss",
    "flask",
    "websockets",
    "zmq",
    "psutil",
    "pupil_labs.realtime_api",
):
    package_datas, package_binaries, package_hiddenimports = collect_all(package)
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_hiddenimports

hiddenimports += collect_submodules("pynput")
hiddenimports += collect_submodules("pupil_labs.realtime_api")
datas += copy_metadata("pupil-labs-realtime-api")

analysis = Analysis(
    [str(ROOT / "main.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pytest", "tkinter"],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(analysis.pure)

executable = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="Atlas",
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
)

COLLECT(
    executable,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    name="Atlas",
)
