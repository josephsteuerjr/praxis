"""Build the Finder entry from source inside an explicit prepared payload."""
from pathlib import Path
import plistlib,subprocess,sys,tempfile

def make_icon(source,destination):
    """Generate Finder's icon from the tracked product PNG, with native tools."""
    with tempfile.TemporaryDirectory(prefix='helene-icon-') as temporary:
        iconset=Path(temporary)/'Helene.iconset';iconset.mkdir()
        for points in (16,32,128,256,512):
            for scale in (1,2):
                pixels=str(points*scale)
                name=f'icon_{points}x{points}'+('@2x' if scale==2 else '')+'.png'
                subprocess.run(['/usr/bin/sips','-z',pixels,pixels,str(source),'--out',str(iconset/name)],
                               check=True,stdout=subprocess.DEVNULL)
        subprocess.run(['/usr/bin/iconutil','-c','icns','-o',str(destination),str(iconset)],check=True)
    if Path(destination).read_bytes()[:4]!=b'icns':raise ValueError('Finder icon is not an ICNS resource')

def build(root,version):
    if sys.platform!='darwin':raise RuntimeError('Finder wrappers require native macOS')
    root=Path(root).resolve();source=Path(__file__).with_name('python_mac_launcher.c')
    for folder,entry,identifier in (('Helene.app','python_app.py','app.helene.python'),):
        bundle=root/folder;binary=bundle/'Contents/MacOS/Helene';binary.parent.mkdir(parents=True,exist_ok=True)
        subprocess.run(['clang','-Os','-mmacosx-version-min=14.0',str(source),'-DHELENE_ENTRY="'+entry+'"',
                        '-framework','CoreFoundation','-o',str(binary)],check=True)
        resources=bundle/'Contents/Resources';resources.mkdir(exist_ok=True)
        make_icon(Path(__file__).resolve().parents[1]/'shell/icons/python-app.png',resources/'icon.icns')
        info=bundle/'Contents/Info.plist'
        metadata=plistlib.loads(info.read_bytes()) if info.exists() else {}
        metadata.update(CFBundleDisplayName='Hélène' if entry=='python_app.py' else 'Перенос Hélène',
            CFBundleName='Hélène' if entry=='python_app.py' else 'Migrate Hélène',CFBundleExecutable='Helene',
            CFBundleIdentifier=identifier,CFBundlePackageType='APPL',CFBundleShortVersionString=version,
            CFBundleVersion=version,CFBundleIconFile='icon',LSMinimumSystemVersion='14.0',NSHighResolutionCapable=True,LSAppNapIsDisabled=True,
            NSMicrophoneUsageDescription='Record a voice message when you choose the microphone.',
            NSLocalNetworkUsageDescription='Connect to your chosen agent or device on your local network.',
            NSAppTransportSecurity={'NSAllowsLocalNetworking':True})
        info.write_bytes(plistlib.dumps(metadata))
        subprocess.run(['codesign','--force','--sign','-','--identifier',identifier,str(bundle)],check=True)
        subprocess.run(['codesign','--verify','--strict',str(bundle)],check=True)

if __name__=='__main__':build(Path(sys.argv[1]),sys.argv[2])
