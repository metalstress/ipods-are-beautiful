"""Read-only device adapters and internal backup helpers. No device SQL writes.

Audio export is intentionally not exposed by the product's library API.
"""
import asyncio
import hashlib
import re
import shlex
import shutil
from pathlib import Path
import stat
import uuid

from library_catalogue import read_snapshot
from library_export import ExportFile
from library_model import LibraryError, selected
from library_plans import assert_fresh
from transport import DATA

CHUNK = 1024 * 1024
MAX_CAPTURE = 256 * 1024**2
TOUCH_LIBRARY = '/var/mobile/Media/iTunes_Control/iTunes/MediaLibrary.sqlitedb'


def device_identity(config):
    backend = 'iphone' if config.get('backend') == 'modern' else 'touch'
    identity = config.get('udid') if backend == 'iphone' else config.get('host_key')
    if not identity:
        raise LibraryError('device_not_configured', 'Configure and select a device first.')
    return backend, backend + '-' + hashlib.sha256(identity.encode()).hexdigest()[:24]


def check_stop(stop):
    if stop is not None and stop.is_set():
        raise LibraryError('cancelled', 'Library operation cancelled.')


def _touch_capture(session, device_id, stop):
    fast = _touch_capture_hashed(session, device_id, stop)
    if fast is not None:
        return fast
    directory = DATA / 'library-captures' / device_id / uuid.uuid4().hex
    directory.mkdir(parents=True)
    previous = None
    for attempt in range(4):
        check_stop(stop)
        before = {}
        for suffix in ('', '-wal', '-shm'):
            try:
                item = session.sftp.lstat(TOUCH_LIBRARY + suffix)
            except FileNotFoundError:
                if suffix:
                    continue
                raise LibraryError('missing_catalogue', 'The device Music catalogue is missing.')
            if not stat.S_ISREG(item.st_mode) or not 0 <= item.st_size <= MAX_CAPTURE:
                raise LibraryError('invalid_catalogue', 'Unexpected catalogue file type or size.')
            before[suffix] = (item.st_size, item.st_mtime, item.st_mode)
        digest = hashlib.sha256()
        for suffix, identity in before.items():
            count = 0
            digest.update(suffix.encode())
            with session.sftp.open(TOUCH_LIBRARY + suffix, 'rb') as source, (directory / ('MediaLibrary.sqlitedb' + suffix)).open('wb') as target:
                while chunk := source.read(CHUNK):
                    check_stop(stop)
                    count += len(chunk)
                    if count > identity[0]:
                        raise LibraryError('catalogue_changed', 'Music catalogue changed while reading.')
                    target.write(chunk)
                    digest.update(chunk)
            if count != identity[0]:
                raise LibraryError('catalogue_changed', 'Music catalogue changed while reading.')
        for suffix in ('', '-wal', '-shm'):
            if suffix not in before:
                (directory / ('MediaLibrary.sqlitedb' + suffix)).unlink(missing_ok=True)
        after = {}
        for suffix in before:
            item = session.sftp.lstat(TOUCH_LIBRARY + suffix)
            after[suffix] = (item.st_size, item.st_mtime, item.st_mode)
        current = digest.digest()
        if before == after and previous == current:
            return directory / 'MediaLibrary.sqlitedb'
        previous = current if before == after else None
    raise LibraryError('catalogue_changed', 'Music catalogue did not settle. Refresh after playback or sync finishes.')


def _touch_capture_hashed(session, device_id, stop):
    """Reuse byte-identical local files; bound the capture by two device hashes.

    A playlist edit usually changes only the WAL. No unchanged multi-megabyte
    database is downloaded again over the iPod's slow Wi-Fi connection.
    """
    if not hasattr(session,'run'):
        return None
    try:
        available=session.run('command -v sha256sum')
    except (RuntimeError,OSError):
        return None
    if not isinstance(available,str) or not available.strip():
        return None
    root=DATA/'library-captures'/device_id
    root.mkdir(parents=True,exist_ok=True)
    directory=root/uuid.uuid4().hex;directory.mkdir()
    cache=DATA/'library-raw-cache'/device_id;cache.mkdir(parents=True,exist_ok=True)
    previous=sorted((p for p in root.iterdir() if p.is_dir() and p!=directory),
                    key=lambda p:p.stat().st_mtime_ns,reverse=True)[:4]
    def identities():
        values={}
        # SHM is a volatile reader index; SQLite rebuilds it locally from WAL.
        for suffix in ('','-wal'):
            try:s=session.sftp.lstat(TOUCH_LIBRARY+suffix)
            except FileNotFoundError:
                if suffix:continue
                raise LibraryError('missing_catalogue','The device Music catalogue is missing.')
            if not stat.S_ISREG(s.st_mode) or not 0<=s.st_size<=MAX_CAPTURE:
                raise LibraryError('invalid_catalogue','Unexpected catalogue file type or size.')
            values[suffix]=(s.st_size,s.st_mtime,s.st_mode)
        return values
    def hashes(values):
        output=session.run('sha256sum '+' '.join(shlex.quote(TOUCH_LIBRARY+s) for s in values),timeout=30)
        result={}
        for line in output.splitlines():
            digest,_,name=line.partition(' ');name=name.strip().lstrip('*')
            if re.fullmatch('[0-9a-f]{64}',digest) and name.startswith(TOUCH_LIBRARY):
                result[name[len(TOUCH_LIBRARY):]]=digest
        if set(result)!=set(values):
            raise LibraryError('catalogue_changed','Music catalogue changed while hashing.')
        return result
    for attempt in range(4):
        check_stop(stop);before=identities();start=hashes(before)
        for suffix,identity in before.items():
            name='MediaLibrary.sqlitedb'+suffix;target=directory/name
            cached=cache/(start[suffix]+suffix)
            if not cached.exists():
                candidate=next((p/name for p in previous if (p/name).is_file()
                    and (p/name).stat().st_size==identity[0]
                    and hashlib.sha256((p/name).read_bytes()).hexdigest()==start[suffix]),None)
                if candidate:shutil.copyfile(candidate,cached)
            if cached.exists() and hashlib.sha256(cached.read_bytes()).hexdigest()==start[suffix]:
                shutil.copyfile(cached,target)
            else:
                digest=hashlib.sha256();count=0
                with session.sftp.open(TOUCH_LIBRARY+suffix,'rb') as source,target.open('wb') as output:
                    while chunk:=source.read(CHUNK):
                        check_stop(stop);count+=len(chunk)
                        if count>identity[0]:raise LibraryError('catalogue_changed','Music catalogue grew during capture.')
                        output.write(chunk);digest.update(chunk)
                if count!=identity[0] or digest.hexdigest()!=start[suffix]:
                    raise LibraryError('catalogue_changed','Music catalogue changed during capture.')
                shutil.copyfile(target,cached)
        for suffix in ('','-wal','-shm'):
            if suffix not in before:(directory/('MediaLibrary.sqlitedb'+suffix)).unlink(missing_ok=True)
        after=identities()
        if before==after and start==hashes(after):
            return directory/'MediaLibrary.sqlitedb'
    raise LibraryError('catalogue_changed','Music catalogue did not settle. Refresh after playback or sync finishes.')


def _read_touch(config, device_id, stop):
    from transport import Session
    with Session(config) as session:
        session.sftp.get_channel().settimeout(30)
        return touch_snapshot(session, device_id, stop)


def touch_snapshot(session, device_id, stop):
    from library_touch_playlists import read
    record = read_snapshot(_touch_capture(session, device_id, stop), 'touch', device_id)
    return read(session, record)


async def _modern(config, device_id):
    from modern_devices import pair_record
    from modern_music import ModernDevice
    record = await pair_record(config['udid']) if config.get('host') else None
    return ModernDevice(config['udid'], DATA / 'library-captures' / device_id,
                        host=config.get('host'), pair_record=record)


def _check_model(device):
    if device.info.get('ProductType') != 'iPhone17,1':
        raise LibraryError('unsupported_device', 'This backend has only been prepared for iPhone 16 Pro.')


async def read_device(config, stop=None):
    backend, device_id = device_identity(config)
    check_stop(stop)
    if backend == 'touch':
        return await asyncio.to_thread(_read_touch, config, device_id, stop)
    async with await _modern(config, device_id) as device:
        _check_model(device)
        await asyncio.wait_for(device.snapshot('library'), 90)
        check_stop(stop)
        return read_snapshot(device.backup / 'MediaLibrary.sqlitedb', backend, device_id,
                             {'anchor': device.catalogue['anchor'], 'library_identity': device.identity,
                              'artwork_ids': sorted(device.catalogue['artwork_ids'])})


def _progress(emit, tracks, completed, transferred, current, track, receipt=None):
    event = {'type': 'library_export', 'stage': 'Exporting', 'track_id': track['id'],
          'tracks_total': len(tracks), 'exported': completed, 'bytes_transferred': transferred + current,
          'bytes_total': sum(t['size_bytes'] for t in tracks)}
    if receipt:
        event['file'] = receipt
    emit(event)


def _export_touch(config, tracks, destination, emit, stop):
    from transport import Session
    result, transferred = [], 0
    with Session(config) as session:
        session.sftp.get_channel().settimeout(30)
        for track in tracks:
            check_stop(stop)
            before = session.sftp.lstat(track['asset_path'])
            if not stat.S_ISREG(before.st_mode) or before.st_size != track['size_bytes']:
                raise LibraryError('asset_changed', 'Audio file differs from the refreshed snapshot.')
            with session.sftp.open(track['asset_path'], 'rb') as source, ExportFile(destination, track) as target:
                while chunk := source.read(CHUNK):
                    check_stop(stop)
                    if target.size + len(chunk) > before.st_size:
                        raise LibraryError('asset_changed', 'Audio file changed while exporting.')
                    target.write(chunk)
                    _progress(emit, tracks, len(result), transferred, target.size, track)
                after = session.sftp.lstat(track['asset_path'])
                if (before.st_size, before.st_mtime) != (after.st_size, after.st_mtime):
                    raise LibraryError('asset_changed', 'Audio file changed while exporting.')
                check_stop(stop)
                result.append(target.finish())
            transferred += track['size_bytes']
            _progress(emit, tracks, len(result), transferred, 0, track, result[-1])
    return {'exported': len(result), 'bytes_transferred': transferred, 'files': result}


async def export_device(config, snapshot, identifiers, destination, emit=lambda e: None, stop=None):
    backend, device_id = device_identity(config)
    if device_id != snapshot['device_id']:
        raise LibraryError('wrong_device', 'The snapshot belongs to another device.')
    tracks = selected(snapshot, identifiers)
    if any(not t['can_export'] for t in tracks):
        raise LibraryError('asset_unavailable', 'Only available, unprotected audio files can be exported.')
    fresh = await read_device(config, stop)
    assert_fresh(snapshot, fresh)
    tracks = selected(fresh, identifiers)
    if backend == 'touch':
        return await asyncio.to_thread(_export_touch, config, tracks, destination, emit, stop)
    results, transferred = [], 0
    async with await _modern(config, device_id) as device:
        _check_model(device)
        for track in tracks:
            check_stop(stop)
            before = await asyncio.wait_for(device.afc.stat(track['asset_path']), 30)
            if before.get('st_ifmt') != 'S_IFREG' or int(before['st_size']) != track['size_bytes']:
                raise LibraryError('asset_changed', 'Audio file differs from the refreshed snapshot.')
            handle = await asyncio.wait_for(device.afc.fopen(track['asset_path'], 'r'), 30)
            try:
                with ExportFile(destination, track) as target:
                    while target.size < track['size_bytes']:
                        check_stop(stop)
                        chunk = await asyncio.wait_for(device.afc.fread(handle, min(CHUNK, track['size_bytes'] - target.size)), 30)
                        if not chunk:
                            raise LibraryError('asset_changed', 'Audio file ended before its expected size.')
                        target.write(chunk)
                        _progress(emit, tracks, len(results), transferred, target.size, track)
                    if await asyncio.wait_for(device.afc.stat(track['asset_path']), 30) != before:
                        raise LibraryError('asset_changed', 'Audio file changed while exporting.')
                    check_stop(stop)
                    results.append(target.finish())
            finally:
                await asyncio.wait_for(device.afc.fclose(handle), 30)
            transferred += track['size_bytes']
            _progress(emit, tracks, len(results), transferred, 0, track, results[-1])
    return {'exported': len(results), 'bytes_transferred': transferred, 'files': results}
