# PyInstaller specification for an Apple Silicon menu-bar .app.
from PyInstaller.utils.hooks import collect_submodules

hidden_imports = (
    collect_submodules("Cocoa")
    + collect_submodules("PyObjCTools")
    + collect_submodules("ApplicationServices")
    + collect_submodules("PIL")
    + collect_submodules("cv2")
    + collect_submodules("telegram")
    + collect_submodules("ai_drive")
    + collect_submodules("apps.telegram_bridge")
    + collect_submodules("apps.overlay")
    + collect_submodules("apps.voice")
    + collect_submodules("ai_drive.automation")
)

a = Analysis(
    ["cohelper.py"],
    pathex=["src", "."],
    binaries=[],
    datas=[("config.example.yaml", ".")],
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="cohelper",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="cohelper")
app = BUNDLE(
    coll,
    name="cohelper.app",
    icon=None,
    bundle_identifier="com.charleschen68.cohelper",
    info_plist={
        "CFBundleDisplayName": "cohelper",
        "CFBundleShortVersionString": "0.1.0",
        "LSMinimumSystemVersion": "14.0",
        "LSUIElement": True,
        "NSHighResolutionCapable": True,
    },
)
