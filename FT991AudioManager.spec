# -*- mode: python ; coding: utf-8 -*-
import os
import re

from PyInstaller.utils.hooks import collect_submodules

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

hiddenimports = ["serial.tools.list_ports"]
hiddenimports += collect_submodules("PySide6")

_main_py = os.path.join(_spec_dir, "main.py")
_icon_ico = os.path.join(_spec_dir, "logo.ico")
_icon_svg = os.path.join(_spec_dir, "logo.svg")

a = Analysis(
    [_main_py],
    pathex=[_spec_dir],
    binaries=[],
    datas=[
        (_icon_ico, "."),
        (_icon_svg, "."),
    ],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
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
