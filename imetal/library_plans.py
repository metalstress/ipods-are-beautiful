"""Snapshot-bound album/track changes. Native shapes follow rustunes (MIT)."""
import base64
from datetime import datetime, timezone
import plistlib
import secrets
import uuid

from library_artwork import prepare_cover
from library_model import LibraryError, capabilities, selected, wire_id


def assert_fresh(expected, actual):
    if any(expected[k] != actual[k] for k in ('device_id', 'library_id', 'revision')):
        raise LibraryError('stale_snapshot', 'The library changed. Refresh it before continuing.')


def plan(snapshot, action, params):
    if not isinstance(params, dict):
        raise LibraryError('invalid_request', 'Mutation parameters must be an object.')
    if action not in ('delete_tracks', 'update_metadata', 'replace_artwork'):
        raise LibraryError('unsupported_operation', 'This library change is not implemented.')
    ids = params.get('track_ids', [])
    if not isinstance(ids, list):
        raise LibraryError('invalid_selection', 'Track selection must be a list.')
    if params.get('album_ids'):
        wanted = params['album_ids']
        if not isinstance(wanted, list) or not wanted or any(not isinstance(i, str) for i in wanted):
            raise LibraryError('invalid_selection', 'Invalid album selection.')
        if not set(wanted) <= {t['album_id'] for t in snapshot['tracks']}:
            raise LibraryError('missing_album', 'Selected album is not in this snapshot.')
        ids = list(dict.fromkeys([*ids, *(t['id'] for t in snapshot['tracks'] if t['album_id'] in wanted)]))
    tracks = selected(snapshot, ids)
    if len(tracks) > 499:
        raise LibraryError('batch_limit', 'A metadata session supports at most 499 tracks.')
    if any(not t['can_plan_delete'] for t in tracks):
        raise LibraryError('read_only_tracks', 'Cloud, pending, protected or unidentified tracks cannot be changed.')
    operations, cover, changes = [], None, None
    if action == 'delete_tracks':
        operations = [{'operation': 'delete_track', 'pid': wire_id(t['wire_id'])} for t in tracks]
    elif action == 'update_metadata':
        changes = params.get('changes')
        if not isinstance(changes, dict) or not changes or set(changes) - {'genre', 'year'}:
            raise LibraryError('unsupported_fields', 'Only genre and release year can be edited.')
        if 'genre' in changes and (not isinstance(changes['genre'], str) or not changes['genre'].strip()
                                  or len(changes['genre']) > 1024 or '\x00' in changes['genre']):
            raise LibraryError('invalid_metadata', 'Genre must contain non-empty text.')
        if 'year' in changes and (type(changes['year']) is not int or not 0 <= changes['year'] <= 9999):
            raise LibraryError('invalid_metadata', 'Release year must be between 0 and 9999.')
        operations = [{'operation': 'update_track', 'pid': wire_id(t['wire_id']), 'item': dict(changes)} for t in tracks]
    else:
        cover = prepare_cover(params.get('cover_path'))
        used = set(snapshot['native'].get('artwork_ids', []))
        for t in tracks:
            cache_id = secrets.randbelow(2**31 - 100) + 100
            while cache_id in used:
                cache_id = secrets.randbelow(2**31 - 100) + 100
            used.add(cache_id)
            operations.append({'operation': 'update_track', 'pid': wire_id(t['wire_id']),
                               'item': {'artwork_cache_id': cache_id, 'artwork_checksum': cover['size_bytes'] + 17}})
    wire = None
    native = snapshot['native']
    if snapshot['backend'] == 'iphone':
        anchor = native.get('anchor')
        if type(anchor) is not int or not 0 <= anchor < 2**32 - 1:
            raise LibraryError('invalid_anchor', 'No valid Music sync revision in this snapshot.')
        database = {'operation': 'update_db_info', 'pid': wire_id(native['db_pid']),
                    'db_info': {'primary_container_pid': wire_id(native['primary_container_pid'])}}
        body = {'revision': anchor + 1, 'timestamp': datetime.now(timezone.utc).replace(tzinfo=None),
                'operations': [database, *operations]}
        data = plistlib.dumps(body, fmt=plistlib.FMT_BINARY, sort_keys=False)
        if len(data) > 4 * 1024**2:
            raise LibraryError('batch_limit', 'The operation exceeds the Music signer size limit.')
        wire = base64.b64encode(data).decode('ascii')
    capability = {'delete_tracks':'delete_tracks', 'update_metadata':'edit_metadata', 'replace_artwork':'replace_artwork'}[action]
    executable = capabilities(snapshot['backend'], False)[capability]['enabled']
    return {'plan_id': uuid.uuid4().hex, 'snapshot_id': snapshot['snapshot_id'], 'revision': snapshot['revision'],
            'device_id': snapshot['device_id'], 'library_id': snapshot['library_id'], 'action': action,
            'track_ids': [t['id'] for t in tracks], 'track_count': len(tracks),
            'size_bytes': sum(t['size_bytes'] for t in tracks) if action == 'delete_tracks' else 0,
            'requires_device_validation': not executable, 'executable': executable, 'native_payload': wire,
            'cover': cover, 'changes': changes,
            'artwork_ids': {t['id']: op['item']['artwork_cache_id'] for t, op in zip(tracks, operations)} if cover else {},
            'side_effects': ['possible_recent_play_counter_reset'] if wire else []}
