"""Execute a single prepared change and verify selected and unrelated tracks."""
import asyncio
import base64
from contextlib import suppress
import json
import time

from library_artwork import cover_bytes
from library_catalogue import read_snapshot
from library_devices import _check_model, _modern, _touch_capture, check_stop, device_identity
from library_model import LibraryError, selected
from library_plans import assert_fresh
from transport import DATA


def verify_change(before, after, proposal):
    if any(before[k] != after[k] for k in ('device_id', 'library_id')):
        raise LibraryError('verification_failed', 'Music library identity changed.')
    old, new = ({t['id']: t for t in s['tracks']} for s in (before, after))
    targets = set(proposal['track_ids'])
    removed = targets if proposal['action'] == 'delete_tracks' else set()
    if set(new) != set(old) - removed:
        raise LibraryError('verification_failed', 'Music track identities differ from the requested change.')
    ignored = {'artwork_id', 'has_artwork'} if proposal['action'] == 'replace_artwork' else set()
    changes = proposal.get('changes') or {}
    for ident, track in new.items():
        expected = dict(old[ident])
        if ident in targets:
            expected.update(changes)
            if proposal['action'] == 'replace_artwork':
                if not track['has_artwork']:
                    raise LibraryError('verification_failed', 'The new cover is not registered in Music.')
                if before['backend'] == 'iphone' and track['artwork_id'] != str(proposal['artwork_ids'][ident]):
                    raise LibraryError('verification_failed', 'Music registered a different cover identity.')
        fields = ignored if ident in targets else set()
        if {k:v for k,v in track.items() if k not in fields} != {k:v for k,v in expected.items() if k not in fields}:
            raise LibraryError('verification_failed', 'Music metadata differs from the requested change.', track_id=ident)
    return after


def _touch(config, snapshot, proposal, emit, stop, cover):
    from library_touch import apply
    from transport import Session
    with Session(config) as session:
        session.sftp.get_channel().settimeout(30)
        fresh = read_snapshot(_touch_capture(session, snapshot['device_id'], stop), 'touch', snapshot['device_id'])
        assert_fresh(snapshot, fresh)
        tracks = selected(fresh, proposal['track_ids'])
        check_stop(stop)
        root = DATA / 'library-journal' / proposal['plan_id']
        root.mkdir(parents=True, exist_ok=True)
        (root/'before.json').write_text(json.dumps(fresh, ensure_ascii=False), encoding='utf-8')
        emit({'type': 'library_change', 'stage': 'Applying', 'total': len(tracks), 'completed': 0})
        # From submission onwards, finish/read back even if the UI asks to cancel.
        # A partially submitted native operation is never retried automatically.
        apply(session, tracks, proposal, cover, fresh['native'].get('genres'))
        for attempt in range(4):
            after = read_snapshot(_touch_capture(session, snapshot['device_id'], None), 'touch', snapshot['device_id'])
            try:
                verify_change(fresh, after, proposal)
                break
            except LibraryError:
                if attempt == 3:
                    raise
                time.sleep(.5)
        (root/'after.json').write_text(json.dumps(after, ensure_ascii=False), encoding='utf-8')
        with suppress(Exception):
            emit({'type': 'connection', **session.info()})
        return after


async def apply_device(config, snapshot, proposal, emit=lambda e: None, stop=None):
    backend, identity = device_identity(config)
    if any(proposal[k] != snapshot[k] for k in ('device_id', 'library_id', 'snapshot_id', 'revision')):
        raise LibraryError('invalid_plan', 'The operation does not match its library snapshot.')
    if identity != snapshot['device_id']:
        raise LibraryError('wrong_device', 'The selected device differs from the library snapshot.')
    cover = cover_bytes(proposal['cover']) if proposal.get('cover') else None
    check_stop(stop)
    if backend == 'touch':
        after = await asyncio.to_thread(_touch, config, snapshot, proposal, emit, stop, cover)
    else:
        from library_modern import submit
        async with await _modern(config, identity) as device:
            _check_model(device)
            await device.snapshot('library-before')
            def capture():
                return read_snapshot(device.backup/'MediaLibrary.sqlitedb', backend, identity,
                    {'anchor': device.catalogue['anchor'], 'library_identity': device.identity,
                     'artwork_ids': sorted(device.catalogue['artwork_ids'])})
            fresh = capture()
            assert_fresh(snapshot, fresh)
            tracks = selected(fresh, proposal['track_ids'])
            check_stop(stop)
            emit({'type': 'library_change', 'stage': 'Applying', 'total': len(tracks), 'completed': 0})
            await submit(device, base64.b64decode(proposal['native_payload'], validate=True),
                         {t['wire_id']: cover for t in tracks} if cover else None, emit)
            after = verify_change(fresh, capture(), proposal)
            if cover:
                from library_covers import original_path
                for track in selected(after, proposal['track_ids']):
                    path = original_path(device.backup/'MediaLibrary.sqlitedb', track['artwork_id'])
                    if not path or await device.read_file(path, limit=32*1024**2) != cover:
                        raise LibraryError('verification_failed', 'The installed artwork bytes differ from the new cover.')
            with suppress(Exception):
                from modern_devices import remember_capacity
                capacity = await asyncio.wait_for(device.afc.get_device_info(), 10)
                free, total = int(capacity['FSFreeBytes']), int(capacity['FSTotalBytes'])
                remember_capacity(config['udid'], free, total)
                emit({'type': 'connection', 'free': free, 'total': total})
    emit({'type': 'library_change', 'stage': 'Completed', 'total': proposal['track_count'],
          'completed': proposal['track_count']})
    return after
