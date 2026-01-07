# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec file for building the MaddiePly GUI executable."""

from __future__ import annotations

import pathlib
import certifi

project_root = pathlib.Path().resolve()
block_cipher = None

# No data files are bundled so the shipped exe never contains .env, credentials,
# settings.txt, or the helper JSON/scripts folder.
datas = [
    (certifi.where(), "certifi"),
]
binaries = []
hiddenimports = [
    "zoneinfo",
    "dotenv.main",
]
excludes = [
    "scripts",
]


a = Analysis(
    ['launcher.py'],
    pathex=[str(project_root)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
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
    name='MaddiePly',
    debug=False,
    bootloader_ignopi_signals=False,
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
    name='MaddiePly'
)
