"""Package a checked .app with native archive permissions and a drag-install DMG."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile


def package(app, output):
    app=app.resolve();output=output.resolve();output.mkdir(parents=True,exist_ok=True)
    archive=output/'iPods-Are-Beautiful-macOS-arm64.zip'
    subprocess.run(['ditto','-c','-k','--sequesterRsrc','--keepParent',str(app),str(archive)],check=True)
    image=output/'iPods-Are-Beautiful-macOS-arm64.dmg'
    with tempfile.TemporaryDirectory(prefix='ipods-dmg-') as temp:
        root=Path(temp)
        subprocess.run(['ditto',str(app),str(root/app.name)],check=True)
        (root/'Applications').symlink_to('/Applications',target_is_directory=True)
        subprocess.run(['hdiutil','create','-volname','iPods Are Beautiful','-srcfolder',str(root),
                        '-ov','-format','UDZO',str(image)],check=True)
    assets=[archive,image]
    hashes={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in assets}
    (output/'SHA256SUMS-macOS-arm64.txt').write_text(''.join(f'{v}  {k}\n' for k,v in hashes.items()),encoding='utf-8')
    print(json.dumps({'assets':[{'name':p.name,'bytes':p.stat().st_size,'sha256':hashes[p.name]} for p in assets]}))


if __name__=='__main__':package(Path(sys.argv[1]),Path(sys.argv[2]))
