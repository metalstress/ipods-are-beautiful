# Native Apple Silicon bundle. Interface assets and widgets are shared unchanged.
import os
from pathlib import Path
import sys

root = Path(SPECPATH)
sys.path.insert(0, str(root))
from media import ffmpeg
from version import VERSION
from PyInstaller.utils.hooks import collect_submodules

a = Analysis([str(root/'app.py')], pathex=[str(root)],
    binaries=[(os.environ['IPODS_MAC_SIGNER'], 'resources'), (ffmpeg(), 'resources')],
    datas=[(os.environ['IPODS_MAC_RESOURCES'], 'resources')],
    hiddenimports=collect_submodules('mutagen'),
    excludes=['imageio_ffmpeg','PySide6.QtWebEngineCore','PySide6.QtWebEngineWidgets',
              'PySide6.QtQuick','PySide6.QtQml','PySide6.QtPdf','PySide6.QtNetworkAuth'])
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='iPods Are Beautiful',
    console=False, target_arch='arm64', codesign_identity='-')
collection = COLLECT(exe, a.binaries, a.datas, name='iPods Are Beautiful')
app = BUNDLE(collection, name='iPods Are Beautiful.app', icon=os.environ['IPODS_MAC_ICON'],
    bundle_identifier='com.metalstress.ipodsarebeautiful', version=VERSION,
    info_plist={
        'CFBundleShortVersionString':VERSION, 'CFBundleVersion':VERSION,
        'LSMinimumSystemVersion':'14.4', 'NSHighResolutionCapable':True,
        'NSRequiresAquaSystemAppearance':True,
        'NSLocalNetworkUsageDescription':'Connect to your iPhone or iPod over Wi-Fi.',
        'NSBonjourServices':['_apple-mobdev2._tcp'],
    })
