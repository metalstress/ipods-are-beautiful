"""Check an ARM64 app offscreen without contacting a connected device."""
import gzip
import hashlib
import json
import os
from pathlib import Path
import platform
import plistlib
import subprocess
import sys
import tempfile

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'imetal'))
from version import VERSION


def check(app, output):
    if sys.platform != 'darwin' or platform.machine() != 'arm64':
        raise RuntimeError('Run this check on an Apple Silicon Mac.')
    app=app.resolve();output=output.resolve();output.mkdir(parents=True,exist_ok=True)
    contents=app/'Contents';executable=contents/'MacOS/iPods Are Beautiful'
    info=plistlib.loads((contents/'Info.plist').read_bytes())
    assert info['CFBundleShortVersionString']==VERSION
    assert info['LSMinimumSystemVersion']=='14.4'
    assert info['NSRequiresAquaSystemAppearance'] is True
    assert '_apple-mobdev2._tcp' in info['NSBonjourServices']
    resources=contents/'Resources/resources'
    if not resources.is_dir():resources=contents/'Frameworks/resources'
    signer=resources/'ipods-music-signer'
    ffmpeg=next(p for p in resources.glob('ffmpeg*') if p.is_file())
    for binary in (executable,signer,ffmpeg):
        assert subprocess.check_output(['lipo','-archs',str(binary)],text=True).split()==['arm64']
    subprocess.run(['codesign','--verify','--deep','--strict',str(app)],check=True)
    defaults=json.loads((resources/'device-defaults.json').read_text())
    assert not defaults.get('ip') and not defaults.get('host_key')
    helper=Path(__file__).resolve().parents[1]/'imetal/resources/imetal-sqlite'
    assert gzip.decompress((resources/'imetal-sqlite.gz').read_bytes())==helper.read_bytes()
    assert not list(app.rglob('*.exe'))
    with tempfile.TemporaryDirectory(prefix='macos-check-',dir=output) as temporary:
        root=Path(temporary)
        env=dict(os.environ,HOME=str(root/'home'),PATH='/usr/bin:/bin:/usr/sbin:/sbin',QT_QPA_PLATFORM='offscreen')
        for key in ('PYTHONHOME','PYTHONPATH','VIRTUAL_ENV','IMAGEIO_FFMPEG_EXE'):
            env.pop(key,None)
        (root/'home').mkdir()
        def run(args):
            result=subprocess.run(args,cwd=root,env=env,capture_output=True,timeout=90)
            if result.returncode:
                raise RuntimeError(result.stderr.decode(errors='replace')[-3000:])
            return result
        for state in ('empty','iphone'):
            image=output.resolve()/('preview-'+state+'.png')
            run([str(executable),'--render',str(image),'--preview-state',state])
            with Image.open(image) as image:
                assert image.width>=800 and image.height>=500
        def job(name,request):
            directory=root/name;directory.mkdir()
            (directory/'request.json').write_text(json.dumps(request),encoding='utf-8')
            run([str(executable),'--backend-job',str(directory)])
            result=json.loads((directory/'result.json').read_text())
            assert not result.get('error'),result
            return result
        assert job('empty',{'command':'transfer','config':{},'paths':[]})['tracks_total']==0
        assert job('repair',{'command':'library.repair_artwork','config':{'backend':'touch'},'payload':{}})['repaired']==0
        from test_library import fixture
        from library_catalogue import read_snapshot
        from library_backend import LibraryCache
        from library_devices import device_identity
        config={'backend':'modern','udid':'packaging-fixture'}
        database=root/'catalogue.db';fixture(database)
        snapshot=read_snapshot(database,'iphone',device_identity(config)[1])
        cache=LibraryCache(root/'home/Library/Application Support/iMetal/library-cache')
        cache.save('snapshots',snapshot)
        assert len(job('browse',{'command':'library.browse','config':config,
            'payload':{'snapshot_id':snapshot['snapshot_id']}})['tracks'])==4
        proposal=job('plan',{'command':'library.plan','config':config,'payload':{
            'snapshot_id':snapshot['snapshot_id'],'action':'create_playlist','name':'Packaging fixture'}})
        assert proposal['executable'] and proposal['action']=='create_playlist'
        assert len(run([str(signer),'request']).stdout)==84
        assert b'ffmpeg version' in run([str(ffmpeg),'-version']).stdout
    report={'version':VERSION,'architecture':'arm64','minimum_macos':'14.4','ad_hoc_signature_verified':True,
        'offscreen_previews':['empty','iphone'],'frozen_backend':True,'frozen_library':True,
        'frozen_artwork_repair':True,'native_signer':True,'native_ffmpeg':True,
        'device_connection_attempted':False,'hardware_tests':False,'notarized':False}
    (output/'release-check.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report),flush=True)


if __name__=='__main__':check(Path(sys.argv[1]),Path(sys.argv[2]))
