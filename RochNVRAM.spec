# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for Roch NVRAM.
# Build with: py -m PyInstaller --clean -y RochNVRAM.spec

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
    version='file_version_info.txt',
)
