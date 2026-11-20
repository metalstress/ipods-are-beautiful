"""Check a frozen Windows build without showing a window or contacting a device."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

import pefile
from PIL import Image
from PyInstaller.archive.readers import CArchiveReader
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'imetal'))
from version import VERSION


def check(executable, output):
    executable = executable.resolve()
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='ipods-release-', dir=output) as temporary:
        root = Path(temporary)
        (root / 'temp').mkdir()
        env = dict(os.environ)
        for name in ('PYTHONHOME', 'PYTHONPATH', 'VIRTUAL_ENV', 'IMAGEIO_FFMPEG_EXE'):
            env.pop(name, None)
        windows = Path(env.get('SYSTEMROOT', 'C:/Windows'))
        env.update(PATH=os.pathsep.join([str(windows / 'System32'), str(windows)]),
                   LOCALAPPDATA=str(root / 'local'), APPDATA=str(root / 'roaming'),
                   TMP=str(root / 'temp'), TEMP=str(root / 'temp'), QT_QPA_PLATFORM='offscreen')

        def run(arguments, **kwargs):
            result = subprocess.run(arguments, cwd=root, env=env, capture_output=True,
                                    creationflags=subprocess.CREATE_NO_WINDOW, timeout=90, **kwargs)
            if result.returncode:
                raise RuntimeError(f'{Path(arguments[0]).name} exited with {result.returncode}: '
                                   + result.stderr.decode(errors='replace')[-1500:])
            return result

        # These preview modes do not start discovery or use the single-instance lock.
        for state in ('empty', 'iphone'):
            image = output / f'preview-{state}.png'
            run([str(executable), '--render', str(image), '--preview-state', state])
            with Image.open(image) as im:
                assert im.width >= 800 and im.height >= 500

        # Exercise the actual frozen child entry point and imports. Empty paths
        # return before pairing, network discovery or opening a device service.
        job = root / 'job'
        job.mkdir()
        (job / 'request.json').write_text(json.dumps({'command': 'transfer', 'config': {}, 'paths': []}), encoding='utf-8')
        run([str(executable), '--backend-job', str(job)])
        result = json.loads((job / 'result.json').read_text(encoding='utf-8'))
        assert result.get('tracks_total') == 0 and result.get('errors') == 0, result

        # Import the idle artwork worker without a supported device config;
        # it must return before any discovery or network/device connection.
        repair = root / 'artwork-repair';repair.mkdir()
        (repair/'request.json').write_text(json.dumps({'command':'library.repair_artwork',
            'config':{'backend':'touch'},'payload':{}}),encoding='utf-8')
        run([str(executable),'--backend-job',str(repair)])
        repaired=json.loads((repair/'result.json').read_text(encoding='utf-8'))
        assert repaired.get('repaired') == 0 and not repaired.get('error'),repaired

        # Exercise frozen library imports and request handling without a device.
        from test_library import fixture
        from library_catalogue import read_snapshot
        from library_backend import LibraryCache
        from library_devices import device_identity
        config={'backend':'modern','udid':'packaging-fixture'}
        database=root/'catalogue.db';fixture(database)
        snapshot=read_snapshot(database,'iphone',device_identity(config)[1])
        cache=LibraryCache(root/'local'/'iMetal'/'library-cache');cache.save('snapshots',snapshot)
        for index,(command,payload) in enumerate([
            ('library.browse',{'snapshot_id':snapshot['snapshot_id']}),
            ('library.plan',{'snapshot_id':snapshot['snapshot_id'],'action':'create_playlist','name':'Packaging fixture'})
        ]):
            request=root/('library-'+str(index));request.mkdir()
            (request/'request.json').write_text(json.dumps({'command':command,'config':config,'payload':payload}),encoding='utf-8')
            run([str(executable),'--backend-job',str(request)])
            response=json.loads((request/'result.json').read_text(encoding='utf-8'))
            assert not response.get('error'),response
            if command=='library.browse':assert len(response['tracks'])==4
            else:assert response['action']=='create_playlist' and response['executable']

        archive = CArchiveReader(str(executable))
        names = {name.replace('\\', '/'): name for name in archive.toc}
        def extract(name):
            return archive.extract(names[name])
        defaults = json.loads(extract('resources/device-defaults.json'))
        assert defaults == {'ip': '', 'mode': 'auto', 'host_key': ''}
        for name in ('resources/licenses/Qt-LGPL-3.0.txt',
                     'resources/licenses/pymobiledevice3-GPL-3.0.txt',
                     'resources/licenses/rustunes-MIT.txt'):
            assert len(extract(name)) > 100
        signer = root / 'signer.exe'
        signer.write_bytes(extract('resources/ipods-music-signer.exe'))
        assert len(run([str(signer), 'request']).stdout) == 84
        ffmpeg_name = next(n for n in names if n.startswith('resources/ffmpeg') and n.endswith('.exe'))
        ffmpeg = root / 'ffmpeg.exe'
        ffmpeg.write_bytes(extract(ffmpeg_name))
        assert b'ffmpeg version 7.1' in run([str(ffmpeg), '-version']).stdout
        assert any(n.endswith('python313.dll') for n in names)
        with pefile.PE(str(executable)) as pe:
            assert pe.FILE_HEADER.Machine == 0x8664
            version = pe.VS_FIXEDFILEINFO[0]
            major,minor,patch=map(int,VERSION.split('.'))
            assert (version.FileVersionMS, version.FileVersionLS) == ((major << 16)|minor,patch << 16)

    report = {'executable': str(executable), 'bytes': executable.stat().st_size,
              'sha256': hashlib.sha256(executable.read_bytes()).hexdigest(),
              'version': VERSION, 'platform': 'Windows x64', 'offscreen_previews': ['empty', 'iphone'],
              'frozen_backend_empty_queue': True, 'packaged_signer': True, 'packaged_ffmpeg': True,
              'frozen_artwork_repair': True,
              'frozen_library_catalogue': True, 'frozen_playlist_planning': True,
              'embedded_python': True, 'blank_device_defaults': True,
              'runtime_path_has_no_python': True, 'device_connection_attempted': False}
    (output / 'release-check.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    return report


if __name__ == '__main__':
    print(json.dumps(check(Path(sys.argv[1]), Path(sys.argv[2]))))
