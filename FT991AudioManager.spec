# -*- mode: python ; coding: utf-8 -*-
import os
import re

# ── Version aus version.py lesen ──────────────────────────────────────────
_spec_dir = os.path.dirname(os.path.abspath(SPEC))
_ver_src = open(os.path.join(_spec_dir, "version.py"), encoding="utf-8").read()
APP_VERSION = re.search(r'APP_VERSION\s*=\s*"([^"]+)"', _ver_src).group(1)
APP_AUTHOR = re.search(r'APP_AUTHOR\s*=\s*"([^"]+)"', _ver_src).group(1)
APP_COPYRIGHT = re.search(r'APP_COPYRIGHT\s*=\s*"([^"]+)"', _ver_src).group(1)

_parts = (APP_VERSION + ".0.0.0").split(".")[:4]
_vi = tuple(int(x) for x in _parts)

_ver_info = f"""VSVersionInfo(
  ffi=FixedFileInfo(
    filevers={_vi},
    prodvers={_vi},
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable('040704b0', [
        StringStruct('CompanyName',      '{APP_AUTHOR}'),
        StringStruct('FileDescription',  'FT-991A Audio-Profilmanager'),
        StringStruct('FileVersion',      '{APP_VERSION}'),
        StringStruct('InternalName',     'FT991AudioManager'),
        StringStruct('LegalCopyright',   '{APP_COPYRIGHT}'),
        StringStruct('OriginalFilename', 'FT991AudioManager.exe'),
        StringStruct('ProductName',      'FT-991A Audio-Profilmanager'),
        StringStruct('ProductVersion',   '{APP_VERSION}'),
      ])
    ]),
    VarFileInfo([VarStruct('Translation', [0x0407, 1200])])
  ]
)"""
with open(os.path.join(_spec_dir, "_version_info.txt"), "w", encoding="utf-8") as _f:
    _f.write(_ver_info)
# ──────────────────────────────────────────────────────────────────────────

# Nur tatsaechlich genutzte Qt-Module (Widgets-GUI + Multimedia fuer Audio-Player).
# NICHT collect_submodules("PySide6") — das zieht alle ~136 Qt-DLLs (~600 MB).
hiddenimports = [
    "serial.tools.list_ports",
    "PySide6.QtMultimedia",
    "PySide6.QtMultimediaWidgets",  # von PyInstaller-Qt-Hook fuer Multimedia-Plugins
    "shiboken6",
    "PySide6.support.deprecated",
]

# Unbenutzte PySide6-Bindings von der Analyse ausschliessen.
excludes = [
    "PySide6.Qt3DAnimation",
    "PySide6.Qt3DCore",
    "PySide6.Qt3DExtras",
    "PySide6.Qt3DInput",
    "PySide6.Qt3DLogic",
    "PySide6.Qt3DRender",
    "PySide6.QtAxContainer",
    "PySide6.QtBluetooth",
    "PySide6.QtCharts",
    "PySide6.QtDataVisualization",
    "PySide6.QtDBus",
    "PySide6.QtDesigner",
    "PySide6.QtGraphs",
    "PySide6.QtGraphsWidgets",
    "PySide6.QtHelp",
    "PySide6.QtHttpServer",
    "PySide6.QtLocation",
    "PySide6.QtNetworkAuth",
    "PySide6.QtNfc",
    "PySide6.QtOpenGL",
    "PySide6.QtOpenGLWidgets",
    "PySide6.QtPdf",
    "PySide6.QtPdfWidgets",
    "PySide6.QtPositioning",
    "PySide6.QtPrintSupport",
    "PySide6.QtQml",
    "PySide6.QtQuick",
    "PySide6.QtQuick3D",
    "PySide6.QtQuickControls2",
    "PySide6.QtQuickWidgets",
    "PySide6.QtRemoteObjects",
    "PySide6.QtScxml",
    "PySide6.QtSensors",
    "PySide6.QtSerialBus",
    "PySide6.QtSerialPort",
    "PySide6.QtSpatialAudio",
    "PySide6.QtSql",
    "PySide6.QtStateMachine",
    "PySide6.QtSvgWidgets",
    "PySide6.QtTest",
    "PySide6.QtUiTools",
    "PySide6.QtWebChannel",
    "PySide6.QtWebEngine",
    "PySide6.QtWebEngineCore",
    "PySide6.QtWebEngineWidgets",
    "PySide6.QtWebSockets",
    "PySide6.QtWebView",
    "PySide6.QtXml",
]

_main_py = os.path.join(_spec_dir, "main.py")
_icon_ico = os.path.join(_spec_dir, "logo.ico")

a = Analysis(
    [_main_py],
    pathex=[_spec_dir],
    binaries=[],
    datas=[
        (_icon_ico, "."),
    ],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="FT991AudioManager",
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
    icon=[_icon_ico],
    version=os.path.join(_spec_dir, "_version_info.txt"),
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="FT991AudioManager",
)
