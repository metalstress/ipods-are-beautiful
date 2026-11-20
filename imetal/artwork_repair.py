"""Idle, one-album artwork repair using the verified library mutation API."""
import base64
from collections import defaultdict
from pathlib import Path
import threading
import uuid

from library_model import LibraryError, groups
from transport import DATA


def missing_albums(snapshot, excluded=()):
    local = [t for t in snapshot['tracks'] if t['availability'] == 'local' and t['can_plan_delete']]
    tracks = defaultdict(list)
    for track in local:
        tracks[track['album_id']].append(track)
    return [(album, tracks[album['id']]) for album in groups(local, 'albums')
            if album['id'] not in excluded and any(not t['has_artwork'] for t in tracks[album['id']])]


def find_artwork(config, snapshot, album, tracks, stop):
    # Prefer an existing album member's own cover when only some songs lack it.
    covered = next((t for t in tracks if t['has_artwork']), None)
    if covered:
        import asyncio
        from library_covers import read_cover
        from device_lock import device_lock
        with device_lock(config):
            existing = asyncio.run(read_cover(config, snapshot, covered['id'], stop))
        if existing.get('path'):
            return Path(existing['path']).read_bytes()
    from online_artwork import find_cover
    for provider in ('deezer', 'archive'):
        if stop.is_set():
            return None
        data = find_cover(album, stop, provider)
        if data:
            return data
    return None


def repair_one_album(config, payload=None, stop=None, service=None):
    """Read afresh after lookup, update only still-missing local tracks, verify."""
    from library_backend import LibraryService
    service = service or LibraryService()
    payload = payload or {}
    stop = stop if stop is not None else threading.Event()
    response = {'repaired': 0, 'checked_album_ids': [], 'remaining': False}
    if config.get('backend') != 'modern' or stop.is_set():
        return response
    try:
        initial = service.execute('library.read', config, {}, stop=stop)
        snapshot = service.cache.load('snapshots', initial['snapshot_id'])
        candidates = missing_albums(snapshot, set(payload.get('exclude_album_ids') or []))
        if payload.get('album_title'):
            candidates = [(a, t) for a, t in candidates if a['title'] == payload['album_title']]
        if not candidates:
            return response
        album, tracks = candidates[0]
        response.update(checked_album_ids=[album['id']], remaining=len(candidates) > 1)
        data = find_artwork(config, snapshot, album, tracks, stop)
        if stop.is_set():
            return {**response, 'checked_album_ids': [], 'cancelled': True}
        if not data:
            return response
        # Another operation may have installed a user-selected cover while the
        # internet lookup ran. Never replace that cover or reuse the old plan.
        current = service.execute('library.read', config, {}, stop=stop)
        fresh = service.cache.load('snapshots', current['snapshot_id'])
        candidates = missing_albums(fresh)
        target = next(((a, t) for a, t in candidates if a['id'] == album['id']
                       and a['title'] == album['title'] and a['album_artist'] == album['album_artist']), None)
        if target is None:
            return response
        missing = [t['id'] for t in target[1] if not t['has_artwork']]
        ids = missing[:499]
        DATA.mkdir(parents=True, exist_ok=True)
        prepared = None
        preserve_input = False
        source = DATA / ('artwork-repair-' + uuid.uuid4().hex + '.image')
        with source.open('xb') as output:
            output.write(data)
        try:
            proposal = service.execute('library.plan', config, {
                'snapshot_id': fresh['snapshot_id'], 'action': 'replace_artwork',
                'track_ids': ids, 'cover_path': str(source)}, stop=stop)
            prepared = service.cache.load('plans', proposal['plan_id'])['cover']
            if stop.is_set():
                return {**response, 'checked_album_ids': [], 'cancelled': True}
            try:
                result = service.execute('library.apply', config, {'plan_id': proposal['plan_id']}, stop=stop)
            except LibraryError as error:
                preserve_input = (error.code == 'change_not_verified'
                                  and error.details.get('cause') not in ('device_busy', 'cancelled', 'stale_snapshot'))
                raise
            from library_artwork import cover_bytes
            installed = cover_bytes(prepared)
            response.update(repaired=len(ids), track_ids=ids,
                artwork=base64.b64encode(installed).decode('ascii'),
                library=service.execute('library.browse', config, {'snapshot_id': result['snapshot_id']}))
            if len(missing) > len(ids):
                response.update(checked_album_ids=[], remaining=True)
        finally:
            source.unlink(missing_ok=True)
            if prepared and not preserve_input:
                # Keep only indeterminate native-operation inputs for recovery.
                # Successful jobs retain no downloaded artwork files.
                path = Path(prepared['path'])
                if path.absolute().parent == (DATA / 'library-artwork').absolute():
                    path.unlink(missing_ok=True)
        return response
    except LibraryError as error:
        if error.code in ('device_busy', 'cancelled', 'stale_snapshot') or error.details.get('cause') in ('device_busy', 'cancelled', 'stale_snapshot'):
            return {**response, 'checked_album_ids': [], 'deferred': True, 'cancelled': stop.is_set()}
        return {**response, 'error': str(error), 'code': error.code}
