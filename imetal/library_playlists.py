"""Snapshot-bound native playlist changes. Audio is never reimported or deleted."""
import asyncio
import base64
from datetime import datetime, timezone
import plistlib
import secrets
import json
import time
import uuid

from library_model import LibraryError, pid, selected, wire_id

ACTIONS = {'create_playlist', 'rename_playlist', 'delete_playlist',
           'add_to_playlist', 'remove_from_playlist', 'set_playlist_tracks'}


def playlist_plan(snapshot, action, params):
    if not snapshot['capabilities'].get('manage_playlists', {}).get('enabled'):
        raise LibraryError('membership_unavailable', 'Playlist membership is unavailable. Refresh the library.')
    old = None
    if action != 'create_playlist':
        ident = pid(params.get('playlist_id'))
        old = next((p for p in snapshot['playlists'] if p['id'] == ident), None)
        if not old or not old.get('can_plan_delete') or old.get('track_ids') is None:
            raise LibraryError('read_only_playlist', 'This playlist cannot be edited.')
    name = params.get('name') if action in ('create_playlist', 'rename_playlist') else old['name']
    if not isinstance(name, str) or not name.strip() or len(name) > 255 or '\x00' in name:
        raise LibraryError('invalid_playlist_name', 'Enter a playlist name of 1–255 characters.')
    name = name.strip()
    members = list(old['track_ids']) if old else []
    tracks = []
    if action in ('add_to_playlist', 'remove_from_playlist', 'set_playlist_tracks') or params.get('track_ids'):
        tracks = [] if action == 'set_playlist_tracks' and params.get('track_ids') == [] else selected(snapshot, params.get('track_ids'))
        if any(not t['can_plan_delete'] for t in tracks):
            raise LibraryError('read_only_tracks', 'Only identified local songs can be added to a playlist.')
    chosen = [t['id'] for t in tracks]
    if action in ('create_playlist', 'add_to_playlist'):
        # Preserve existing occurrences/order, but never add a second copy.
        present = set(members)
        for ident in chosen:
            if ident not in present:
                members.append(ident);present.add(ident)
    elif action == 'remove_from_playlist':
        members = [i for i in members if i not in set(chosen)]
    elif action == 'set_playlist_tracks':
        members = chosen
    if len(members) > 10000:
        raise LibraryError('batch_limit', 'A playlist supports up to 10000 songs in this version.')
    used = set(snapshot['native'].get('occupied_wire_ids', [])) | {p['id'] for p in snapshot['playlists']}
    identity = old['id'] if old else str(secrets.randbelow(2**63 - 1000) + 1000)
    while not old and identity in used:
        identity = str(secrets.randbelow(2**63 - 1000) + 1000)
    wire = None
    if snapshot['backend'] == 'iphone':
        native = snapshot['native']
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        operation = {'operation': 'delete_playlist', 'pid': wire_id(old['wire_id'])} if action == 'delete_playlist' else {
            'operation': 'insert_playlist' if old is None else 'update_playlist',
            'pid': wire_id(old['wire_id'] if old else identity),
            'container': {'name': name, 'date_modified': now},
            'item_to_container': []}
        if action != 'delete_playlist':
            lookup = {t['id']: t for t in snapshot['tracks']}
            if any(i not in lookup or lookup[i]['wire_id'] == '0' for i in members):
                raise LibraryError('read_only_playlist', 'This playlist contains unsupported song references.')
            operation['item_to_container'] = [wire_id(lookup[i]['wire_id']) for i in members]
            if old is None:
                operation['container']['date_created'] = now
                operation['container_ui'] = {'play_order': 1, 'album_field_order': 0, 'repeat_mode': 0,
                    'is_reversed': False, 'shuffle_items': False, 'has_been_shuffled': False}
        anchor = native.get('anchor')
        if type(anchor) is not int or not 0 <= anchor < 2**32-1:
            raise LibraryError('invalid_anchor', 'Music sync revision is unavailable.')
        data = plistlib.dumps({'revision': anchor+1, 'timestamp': now, 'operations': [
            {'operation': 'update_db_info', 'pid': wire_id(native['db_pid']),
             'db_info': {'primary_container_pid': wire_id(native['primary_container_pid'])}}, operation
        ]}, fmt=plistlib.FMT_BINARY, sort_keys=False)
        if len(data) > 4*1024**2:
            raise LibraryError('batch_limit', 'The playlist exceeds the Music signer size limit.')
        wire = base64.b64encode(data).decode('ascii')
    return {'plan_id': uuid.uuid4().hex, 'snapshot_id': snapshot['snapshot_id'], 'revision': snapshot['revision'],
        'device_id': snapshot['device_id'], 'library_id': snapshot['library_id'], 'action': action,
        'playlist_id': old['id'] if old else None, 'new_playlist_id': identity if old is None else None,
        'playlist_name': name, 'member_ids': members, 'track_ids': chosen, 'track_count': len(chosen),
        'size_bytes': 0, 'executable': True, 'requires_device_validation': False,
        'noop': bool(old and action != 'delete_playlist' and old['name'] == name and old['track_ids'] == members),
        'native_payload': wire, 'cover': None, 'changes': None,
        'side_effects': ['possible_recent_play_counter_reset'] if wire else []}


def verify_playlist(before, after, proposal, created_id=None):
    if any(before[k] != after[k] for k in ('device_id', 'library_id')) or before['tracks'] != after['tracks']:
        raise LibraryError('verification_failed', 'Songs changed during a playlist-only operation.')
    old = {p['id']: p for p in before['playlists']}
    new = {p['id']: p for p in after['playlists']}
    ident = pid(created_id or proposal.get('new_playlist_id') or proposal['playlist_id'])
    expected_ids = (set(old) | {ident}) if proposal['action'] == 'create_playlist' else (
        set(old)-{ident} if proposal['action'] == 'delete_playlist' else set(old))
    if set(new) != expected_ids:
        raise LibraryError('verification_failed', 'The playlist catalogue differs from the requested change.')
    for key, record in new.items():
        if key != ident:
            fields = ('name', 'track_ids', 'kind', 'is_hidden')
            if any(record[f] != old[key][f] for f in fields):
                raise LibraryError('verification_failed', 'An unrelated playlist changed.')
        elif record['name'] != proposal['playlist_name'] or record['track_ids'] != proposal['member_ids']:
            raise LibraryError('verification_failed', 'Playlist name or song order was not saved.')
    return after


async def apply_playlist(config, snapshot, proposal, emit, stop):
    from library_devices import check_stop, device_identity, _modern, _check_model, touch_snapshot
    from library_plans import assert_fresh
    backend, identity = device_identity(config)
    if identity != snapshot['device_id'] or any(proposal[k] != snapshot[k] for k in
            ('snapshot_id', 'device_id', 'library_id', 'revision')):
        raise LibraryError('invalid_plan', 'This playlist change belongs to another snapshot.')
    check_stop(stop)
    if backend == 'touch':
        def run():
            from transport import Session
            from library_touch_playlists import apply
            with Session(config) as session:
                session.sftp.get_channel().settimeout(30)
                fresh = touch_snapshot(session, identity, stop)
                assert_fresh(snapshot, fresh);check_stop(stop)
                if proposal.get('noop'):
                    return fresh
                emit({'type': 'library_change', 'stage': 'Saving playlist'})
                result = apply(session, fresh, proposal)
                from transport import DATA
                journal=DATA/'library-journal'/proposal['plan_id'];journal.mkdir(parents=True,exist_ok=True)
                (journal/'playlist-before.json').write_text(json.dumps(fresh,ensure_ascii=False),encoding='utf-8')
                for attempt in range(4):
                    after = touch_snapshot(session, identity, None)
                    (journal/'playlist-after.json').write_text(json.dumps(after,ensure_ascii=False),encoding='utf-8')
                    try:return verify_playlist(fresh, after, proposal, result.get('created_id'))
                    except LibraryError:
                        if attempt==3:raise
                        time.sleep(.5)
        after = await asyncio.to_thread(run)
    else:
        from library_catalogue import read_snapshot
        from library_modern import submit
        async with await _modern(config, identity) as device:
            _check_model(device)
            await device.snapshot('playlist-before')
            def capture():
                return read_snapshot(device.backup/'MediaLibrary.sqlitedb', backend, identity,
                    {'anchor': device.catalogue['anchor'], 'library_identity': device.identity,
                     'artwork_ids': sorted(device.catalogue['artwork_ids'])})
            fresh = capture();assert_fresh(snapshot, fresh);check_stop(stop)
            if proposal.get('noop'):
                return fresh
            emit({'type': 'library_change', 'stage': 'Saving playlist'})
            await submit(device, base64.b64decode(proposal['native_payload'], validate=True), emit=emit)
            after = verify_playlist(fresh, capture(), proposal)
    emit({'type': 'library_change', 'stage': 'Completed', 'completed': 1, 'total': 1})
    return after
