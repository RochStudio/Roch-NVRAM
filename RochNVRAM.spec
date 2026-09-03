# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for Roch NVRAM.
# Build with: py -m PyInstaller --clean -y RochNVRAM.spec

import os
import sys

# bios_manager/version.py is the only place the name and version are written.
# The Windows version resource is generated from it here rather than kept as a
# second copy in the repository, so a release cannot ship an executable whose
# properties disagree with the program inside it.
sys.path.insert(0, SPECPATH)
from bios_manager.version import APP_NAME, VERSION_TUPLE, __version__

_numbers = ", ".join(str(part) for part in VERSION_TUPLE)
_dotted = ".".join(str(part) for part in VERSION_TUPLE)
_version_resource = os.path.join(workpath, "file_version_info.txt")
os.makedirs(workpath, exist_ok=True)
with open(_version_resource, "w", encoding="utf-8") as _stream:
    _stream.write("""VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=({numbers}),
    prodvers=({numbers}),
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0),
  ),
  kids=[
    StringFileInfo([
      StringTable('040904B0', [
        StringStruct('CompanyName', 'Roch Studio'),
        StringStruct('FileDescription', '{name}'),
        StringStruct('FileVersion', '{dotted}'),
        StringStruct('InternalName', 'RochNVRAM'),
        StringStruct('OriginalFilename', 'RochNVRAM.exe'),
        StringStruct('ProductName', '{name}'),
        StringStruct('ProductVersion', '{dotted}'),
      ]),
    ]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])]),
  ],
)
""".format(numbers=_numbers, dotted=_dotted, name=APP_NAME))

# Files the app opens at runtime.
#
# The icon goes inside the bundle, because gui.asset_path() looks in
# sys._MEIPASS when frozen.
#
# scewin/ deliberately does not. app.base_dir() anchors on the executable's
# own directory when frozen, so SCEWIN_64.exe and the two drivers sit beside
# RochNVRAM.exe rather than inside it. That is what makes them replaceable:
# anyone who would rather not take the bundled AMI binaries on trust can drop
# in their own copies without rebuilding. See THIRD_PARTY_NOTICES.txt.
datas = [
    ('assets/roch_nvram.ico', 'assets'),
    # Quick Settings presets are read through the same asset root as the icon.
    ('assets/quick_settings/*.json', 'assets/quick_settings'),
]

hiddenimports = [
    # Imported inside main() so the missing-PySide6 message can be printed
    # before Qt is touched, which hides it from the dependency scan.
    'bios_manager.gui',
]

# PySide6 ships far more than this program uses. WebEngine alone is most of
# the install, and none of these are imported anywhere in bios_manager.
excludes = [
    'PySide6.QtWebEngineCore',
    'PySide6.QtWebEngineWidgets',
    'PySide6.QtWebEngineQuick',
    'PySide6.QtQuick',
    'PySide6.QtQuick3D',
    'PySide6.QtQml',
    'PySide6.Qt3DCore',
    'PySide6.Qt3DRender',
    'PySide6.QtCharts',
    'PySide6.QtDataVisualization',
    'PySide6.QtMultimedia',
    'PySide6.QtMultimediaWidgets',
    'PySide6.QtBluetooth',
    'PySide6.QtNfc',
    'PySide6.QtPositioning',
    'PySide6.QtSensors',
    'PySide6.QtSerialPort',
    'PySide6.QtSql',
    'PySide6.QtTest',
    'PySide6.QtDesigner',
    'PySide6.QtHelp',
    'PySide6.QtPdf',
    'PySide6.QtPdfWidgets',
    'PySide6.QtSpatialAudio',
    'PySide6.QtTextToSpeech',
    'tkinter',
]

a = Analysis(
    ['app.py'],
    pathex=[],
    binaries=[],
    datas=datas,
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
    a.binaries,
    a.datas,
    [],
    name='RochNVRAM',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    # A GUI program: no console window behind it.
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='assets/roch_nvram.ico',
    # SCEWIN needs administrator rights the moment Export or Import runs, and a
    # running process cannot elevate itself, so the manifest asks up front.
    uac_admin=True,
    version=_version_resource,
)
