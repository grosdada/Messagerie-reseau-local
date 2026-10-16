# Run from repository root: pyinstaller macos/MessagerieLocale.spec
from PyInstaller.utils.hooks import collect_submodules
from pathlib import Path
root = Path(SPECPATH).parent
a = Analysis([str(root / 'macos' / 'lan_messenger_mac.py')], pathex=[str(root)], hiddenimports=collect_submodules('webview'), excludes=['tkinter'])
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='MessagerieLocale', console=False)
coll = COLLECT(exe, a.binaries, a.datas, name='MessagerieLocale')
app = BUNDLE(coll, name='Messagerie locale.app', bundle_identifier='fr.grosdada.messagerielocale', info_plist={
    'CFBundleShortVersionString': '1.1.1',
    'NSLocalNetworkUsageDescription': 'Découvrir les PC du réseau local et échanger des messages et des images sans Internet.',
    'NSHighResolutionCapable': True,
})
