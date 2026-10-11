# -*- mode: python ; coding: utf-8 -*-
# UDF -> PDF sag-tik donusturucu. onedir (hizli baslangic; merge cok surec acar).

import os

a = Analysis(
    ['udf_to_pdf.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('icons', 'icons'),
    ],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'PySide6.QtPdf', 'PySide6.QtQml', 'PySide6.QtQuick',
        'PySide6.QtNetwork', 'PySide6.QtWebEngineCore', 'PySide6.Qt3DCore',
        'PySide6.QtMultimedia', 'PySide6.QtCharts', 'PySide6.QtSql',
    ],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='udf2pdf',
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
    icon=['solo-logo.ico'],
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='udf2pdf',
)

# Kurulum/kaldırma .bat dosyaları exe'nin YANINA kopyalanır: `datas` ile verilince
# PyInstaller 6 onları `_internal/` içine koyar ve `%~dp0udf2pdf.exe` bulunamaz.
import shutil
for _bat in ('Kurulum-SagTik-PDF.bat', 'Kaldir-SagTik-PDF.bat'):
    shutil.copy2(os.path.join('installer', _bat), os.path.join(DISTPATH, 'udf2pdf', _bat))
