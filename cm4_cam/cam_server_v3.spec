# -*- mode: python ; coding: utf-8 -*-

# このファイルはCM4カメラサーバー v3 を単体実行ファイルへ変換する PyInstaller 設定を担当する。
import os


a = Analysis(
    [os.path.join(SPECPATH, 'cam_server_v3.py')],
    pathex=[],
    binaries=[],
    datas=[(os.path.join(SPECPATH, 'default_hsv_config.json'), '.')],
    hiddenimports=[],
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
    name='cam_server_v3',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
