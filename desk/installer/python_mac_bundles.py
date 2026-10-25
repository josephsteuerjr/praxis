"""Build both Finder entries from source inside an explicit prepared payload."""
from pathlib import Path
import plistlib,subprocess,sys

def build(root,version):
    if sys.platform!='darwin':raise RuntimeError('Finder wrappers require native macOS')
    root=Path(root).resolve();source=Path(__file__).with_name('python_mac_launcher.c')
    for folder,entry,identifier in (('Helene.app','python_app.py','app.helene.python'),
                                    ('Migrate Helene.app','mac_migrate.py','app.helene.python.migrate')):
        bundle=root/folder;binary=bundle/'Contents/MacOS/Helene';binary.parent.mkdir(parents=True,exist_ok=True)
        subprocess.run(['clang','-Os','-mmacosx-version-min=14.0',str(source),'-DHELENE_ENTRY="'+entry+'"',
                        '-framework','CoreFoundation','-o',str(binary)],check=True)
        info=bundle/'Contents/Info.plist'
        metadata=plistlib.loads(info.read_bytes()) if info.exists() else {}
        metadata.update(CFBundleDisplayName='Hélène' if entry=='python_app.py' else 'Перенос Hélène',
            CFBundleName='Hélène' if entry=='python_app.py' else 'Migrate Hélène',CFBundleExecutable='Helene',
            CFBundleIdentifier=identifier,CFBundlePackageType='APPL',CFBundleShortVersionString=version,
            CFBundleVersion=version,LSMinimumSystemVersion='14.0',NSHighResolutionCapable=True,LSAppNapIsDisabled=True,
            NSMicrophoneUsageDescription='Record a voice message when you choose the microphone.',
            NSLocalNetworkUsageDescription='Connect to your chosen agent or device on your local network.',
            NSAppTransportSecurity={'NSAllowsLocalNetworking':True})
        info.write_bytes(plistlib.dumps(metadata))
        subprocess.run(['codesign','--force','--sign','-','--identifier',identifier,str(bundle)],check=True)
        subprocess.run(['codesign','--verify','--strict',str(bundle)],check=True)

if __name__=='__main__':build(Path(sys.argv[1]),sys.argv[2])
