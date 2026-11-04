"""UI-independent library records, stable identities and paginated queries."""
import hashlib
import json
import re
from pathlib import PurePosixPath


class LibraryError(RuntimeError):
    def __init__(self, code, message, **details):
        super().__init__(message)
        self.code, self.details = code, details


def pid(value):
    """Public IDs are decimal strings; never pass 64-bit IDs through JS numbers."""
    if type(value) is int:
        number = value
    elif isinstance(value, str) and re.fullmatch(r'-?[0-9]+', value):
        number = int(value)
    else:
        raise LibraryError('invalid_id', 'Invalid library identifier.')
    if not -(2**63) <= number < 2**64:
        raise LibraryError('invalid_id', 'Library identifier is outside 64-bit bounds.')
    return str(number & (2**64 - 1))


def wire_id(value):
    number = int(pid(value))
    return number - 2**64 if number >= 2**63 else number


def asset_path(backend, base, location):
    if not location:
        return None
    if any('\\' in s or '\x00' in s or ':' in s or '..' in PurePosixPath(s).parts
           for s in (base, location)):
        return None
    root = '/var/mobile/Media' if backend == 'touch' else '/'
    path = PurePosixPath(root) / base / location
    prefixes = [root.rstrip('/') + '/iTunes_Control/Music/']
    if backend == 'touch':
        # StoreServices installs app-imported songs here on iOS 6; older
        # iTunes-synced music stays under iTunes_Control/Music.
        prefixes.append(root.rstrip('/') + '/Purchases/')
    text = str(path)
    return text if any(text.startswith(prefix) for prefix in prefixes) else None


def capabilities(backend, membership_known):
    return {
        'read': {'enabled': True, 'validation': 'hardware_usb_wifi'},
        'playlist_members': {'enabled': membership_known, 'validation': 'offline',
                             'reason': None if membership_known else 'legacy_virtual_table'},
        'delete_tracks': {'enabled': True, 'planning': True, 'validation': 'hardware_usb_wifi'},
        'edit_metadata': {'enabled': True, 'fields': ['genre', 'year'], 'validation': 'hardware', 'date_precision': 'year'},
        'replace_artwork': {'enabled': True, 'validation': 'hardware'},
    }


def seal(snapshot):
    # Captured time, local cache paths and capability validation do not describe
    # library contents and must not invalidate selections on their own.
    body = {k: snapshot[k] for k in ('backend', 'device_id', 'library_id', 'tracks', 'playlists', 'native')}
    snapshot['revision'] = hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=True,
                                                    separators=(',', ':')).encode()).hexdigest()
    return snapshot


def summary(snapshot):
    tracks = snapshot['tracks']
    return {k: snapshot[k] for k in ('snapshot_id', 'revision', 'backend', 'device_id',
                                      'captured_at', 'capabilities')} | {
        'counts': {'tracks': len(tracks), 'local': sum(t['availability'] == 'local' for t in tracks),
                   'albums': len({t['album_id'] for t in tracks}),
                   'artists': len({t['artist_id'] for t in tracks}), 'playlists': len(snapshot['playlists']),
                   'bytes': sum(t['size_bytes'] for t in tracks if t['availability'] == 'local'),
                   'duration_ms': sum(t['duration_ms'] for t in tracks)}}


def selected(snapshot, identifiers):
    if not isinstance(identifiers, list) or not identifiers or len(identifiers) > 10000:
        raise LibraryError('invalid_selection', 'Select between 1 and 10000 tracks.')
    ids = [pid(i) for i in identifiers]
    if len(set(ids)) != len(ids):
        raise LibraryError('invalid_selection', 'The selection contains repeated tracks.')
    lookup = {t['id']: t for t in snapshot['tracks']}
    missing = [i for i in ids if i not in lookup]
    if missing:
        raise LibraryError('missing_tracks', 'Selected tracks are no longer in this snapshot.', ids=missing)
    return [lookup[i] for i in ids]


def groups(tracks, kind):
    result = {}
    key = 'album_id' if kind == 'albums' else 'artist_id'
    for t in tracks:
        row = result.setdefault(t[key], {'id': t[key], 'title': t['album'] if kind == 'albums' else t['artist'],
                                        'album_artist': t['album_artist'] if kind == 'albums' else '',
                                        'track_count': 0, 'local_count': 0, 'size_bytes': 0, 'duration_ms': 0,
                                        'year': t['year'], 'has_artwork': False, 'artwork_track_id': None,
                                        '_years': set(), '_genres': set()})
        row['track_count'] += 1
        row['local_count'] += t['availability'] == 'local'
        row['size_bytes'] += t['size_bytes'] if t['availability'] == 'local' else 0
        row['duration_ms'] += t['duration_ms']
        row['has_artwork'] |= t['has_artwork']
        row['_years'].add(t['year']);row['_genres'].add(t['genre'])
        if t['has_artwork'] and row['artwork_track_id'] is None:
            row['artwork_track_id'] = t['id']
    for row in result.values():
        years, genres = row.pop('_years'), row.pop('_genres')
        row['metadata_mixed'] = {'year': len(years) > 1, 'genre': len(genres) > 1}
        row['year'] = next(iter(years)) if len(years) == 1 else None
        row['genre'] = next(iter(genres)) if len(genres) == 1 else None
    return list(result.values())


def query(snapshot, params):
    kind = params.get('kind', 'tracks')
    if kind not in ('tracks', 'albums', 'artists', 'playlists'):
        raise LibraryError('invalid_query', 'Unknown library view.')
    offset, limit = params.get('offset', 0), params.get('limit', 100)
    if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 500:
        raise LibraryError('invalid_query', 'Invalid page bounds.')
    text = params.get('search', '')
    if not isinstance(text, str) or len(text) > 256:
        raise LibraryError('invalid_query', 'Search is limited to 256 characters.')
    tracks = snapshot['tracks']
    for key in ('album_id', 'artist_id'):
        if key in params:
            tracks = [t for t in tracks if t[key] == params[key]]
    playlist_order = None
    if 'playlist_id' in params:
        playlist = next((p for p in snapshot['playlists'] if p['id'] == pid(params['playlist_id'])), None)
        if playlist is None:
            raise LibraryError('missing_playlist', 'Playlist is not in this snapshot.')
        if playlist['track_ids'] is None:
            raise LibraryError('membership_unavailable', 'This legacy playlist needs the device-side membership reader.')
        lookup = {t['id']: t for t in tracks}
        tracks = [lookup[i] for i in playlist['track_ids'] if i in lookup]
        playlist_order = True
    rows = list(tracks) if kind == 'tracks' else groups(tracks, kind) if kind != 'playlists' else snapshot['playlists']
    if text.strip():
        needle = text.strip().casefold()
        rows = [r for r in rows if needle in ' '.join(str(r.get(k, '')) for k in
                ('title', 'name', 'artist', 'album', 'album_artist', 'genre')).casefold()]
    sort = params.get('sort')
    allowed = {'tracks': {'title', 'artist', 'album', 'year', 'track_number', 'size_bytes', 'duration_ms'},
               'albums': {'title', 'year', 'track_count', 'size_bytes', 'duration_ms'},
               'artists': {'title', 'track_count', 'size_bytes', 'duration_ms'}, 'playlists': {'name', 'track_count'}}
    if sort is None and not playlist_order:
        sort = 'name' if kind == 'playlists' else 'title'
    if sort is not None:
        if sort not in allowed[kind] or type(params.get('descending', False)) is not bool:
            raise LibraryError('invalid_query', 'Unknown sort field or direction.')
        def key(row):
            value = row.get(sort)
            return (value is None, value.casefold() if isinstance(value, str) else value or 0)
        rows = sorted(rows, key=key, reverse=params.get('descending', False))
    # Paths and wire identities stay in the cache/backend, never in table rows.
    private = {'asset_path', 'wire_id', 'album_wire_id', 'track_ids', 'artwork_id', 'can_export'}
    return {'snapshot_id': snapshot['snapshot_id'], 'revision': snapshot['revision'], 'kind': kind,
            'total': len(rows), 'offset': offset, 'limit': limit,
            'items': [{k: v for k, v in r.items() if k not in private} for r in rows[offset:offset + limit]]}
