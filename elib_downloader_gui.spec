# -*- mode: python ; coding: utf-8 -*-
import os, importlib

# Locate customtkinter package for bundling its assets
ctk_path = os.path.dirname(importlib.import_module("customtkinter").__file__)

a = Analysis(
    ['main_gui.py'],
    pathex=[],
    binaries=[],
    datas=[
        (ctk_path, 'customtkinter'),
    ],
    hiddenimports=[
        'customtkinter',
        'PIL',
        'PIL._tkinter_finder',
    ],
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
    a.binaries,
    a.datas,
    [],
    name='elib_downloader',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,          # No console window!
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,              # Set your .ico path here if you have one
)
