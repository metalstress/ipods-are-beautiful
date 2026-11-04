"""Read artwork for the selected local track; caches images for the future UI."""
import asyncio
from contextlib import closing
import hashlib
import io
from pathlib import PurePosixPath
import sqlite3
import uuid

from library_devices import _check_model, _modern, check_stop, device_identity
from library_model import LibraryError, selected
from transport import DATA


def original_path(database, token):
    with closing(sqlite3.connect(database.resolve().as_uri() + '?mode=ro', uri=True)) as db:
        row = db.execute('select relative_path from artwork where artwork_token=? and artwork_source_type=300 limit 1', (token,)).fetchone()
    if not row or not row[0]:
        return None
    relative = PurePosixPath(row[0])
    if relative.is_absolute() or '..' in relative.parts or '\\' in str(relative) or ':' in str(relative):
        raise LibraryError('invalid_artwork', 'Unexpected Music artwork path.')
    return '/iTunes_Control/iTunes/Artwork/Originals/' + str(relative)


async def read_cover(config, snapshot, identifier, stop=None):
    backend, identity = device_identity(config)
    if identity != snapshot['device_id']:
        raise LibraryError('wrong_device', 'The selected device differs from this artwork snapshot.')
    track = selected(snapshot, [identifier])[0]
    check_stop(stop)
    if not track['has_artwork']:
        return {'path': None}
    key = hashlib.sha256((identity + snapshot['revision'] + track['id']).encode()).hexdigest()
    root = DATA / 'library-artwork'
    root.mkdir(parents=True, exist_ok=True)
    path = root / (key + ('.png' if backend == 'touch' else '.jpg'))
    if not path.exists():
        if backend == 'touch':
            from library_touch import artwork
            from transport import Session
            def capture():
                with Session(config) as session:
                    session.sftp.get_channel().settimeout(30)
                    return artwork(session, track)
            data = await asyncio.to_thread(capture)
        else:
            async with await _modern(config, identity) as device:
                _check_model(device)
                await device.snapshot('library-cover')
                remote = original_path(device.backup/'MediaLibrary.sqlitedb', track['artwork_id'])
                data = await device.read_file(remote, limit=32*1024**2) if remote else None
        check_stop(stop)
        if not data:
            return {'path': None}
        if len(data) > 32*1024**2:
            raise LibraryError('invalid_artwork', 'The device cover is too large.')
        from PIL import Image
        with Image.open(io.BytesIO(data)) as image:
            image.verify()
        temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.part')
        try:
            with temporary.open('xb') as target:
                target.write(data)
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
    return {'path': str(path), 'track_id': track['id'], 'revision': snapshot['revision']}
