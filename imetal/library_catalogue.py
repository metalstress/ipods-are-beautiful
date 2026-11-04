"""Read local, captured catalogues. Never open the device database for writing."""
from datetime import datetime, timezone
from contextlib import closing
import hashlib
from pathlib import Path
import sqlite3
import uuid

from library_model import LibraryError, asset_path, capabilities, pid, seal

MAX_TRACKS = 100000
MAX_PLAYLISTS = 10000
MAX_MEMBERS = 500000


def read_snapshot(path, backend, device_id, native=None):
    if backend not in ('touch', 'iphone'):
        raise LibraryError('unsupported_device', 'No library adapter for this device.')
    with closing(sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)) as db:
        db.row_factory = sqlite3.Row
        db.execute('pragma query_only=ON')
        schema = db.execute('pragma user_version').fetchone()[0]
        supported = (1140117,) if backend == 'touch' else (2400060, 2410000)
        if schema not in supported:
            raise LibraryError('unsupported_schema', 'Untested Music library schema.', schema=schema)
        if db.execute('pragma quick_check').fetchone()[0] != 'ok':
            raise LibraryError('invalid_catalogue', 'Music catalogue integrity check failed.')
        definitions = dict(db.execute("select name,sql from sqlite_master where type='table'"))
        def columns(table):
            if table not in definitions or 'VIRTUAL TABLE' in (definitions[table] or '').upper():
                return set()
            return {r[1] for r in db.execute('pragma table_info(' + table + ')')}
        schema_columns = {t: columns(t) for t in ('item', 'item_extra', 'item_store', 'item_artist', 'album',
                                                  'album_artist', 'genre', 'base_location', 'container', 'container_item')}
        for table, required in {'item': {'item_pid'}, 'item_extra': {'item_pid', 'title', 'location', 'file_size'},
                                'db_info': {'db_pid', 'primary_container_pid'}}.items():
            if not required <= (columns(table)):
                raise LibraryError('unsupported_schema', 'Required library fields are missing.', table=table)
        identities = db.execute('select db_pid,primary_container_pid from db_info').fetchall()
        info = identities[0] if len(identities) == 1 else None
        if not info or not info['db_pid']:
            raise LibraryError('invalid_catalogue', 'Music library identity is missing.')
        joins = []
        specs = [('s', 'item_store', 'item_pid'), ('a', 'item_artist', 'item_artist_pid'),
                 ('b', 'album', 'album_pid'), ('aa', 'album_artist', 'album_artist_pid'),
                 ('g', 'genre', 'genre_id'), ('l', 'base_location', 'base_location_id')]
        for alias, table, key in specs:
            if key in schema_columns[table] and key in schema_columns['item']:
                joins.append(f'left join {table} {alias} on {alias}.{key}=i.{key}')
        aliases = {'i': 'item', 'e': 'item_extra'} | {a: t for a, t, k in specs
                                                     if k in schema_columns[t] and k in schema_columns['item']}
        def field(alias, name, fallback="''"):
            return f'coalesce({alias}.{name},{fallback})' if alias in aliases and name in schema_columns[aliases[alias]] else fallback
        fields = {'id': 'i.item_pid', 'title': 'e.title', 'artist': field('a', 'item_artist'),
                  'album': field('b', 'album'), 'album_artist': field('aa', 'album_artist'),
                  'genre': field('g', 'genre'), 'album_pid': field('i', 'album_pid', '0'),
                  'artist_pid': field('i', 'item_artist_pid', '0'), 'wire_id': field('s', 'sync_id', '0'),
                  'album_wire_id': field('b', 'sync_id', '0'), 'base': field('l', 'path'),
                  'location': 'e.location', 'size_bytes': 'e.file_size', 'duration_ms': field('e', 'total_time_ms', '0'),
                  'year': field('e', 'year', '0'), 'track_number': field('i', 'track_number', '0'),
                  'disc_number': field('i', 'disc_number', '0'), 'artwork': field('e', 'artwork_cache_id',
                    field('s', 'artwork_url', "''"))}
        fields['artwork_id'] = field('e', 'artwork_cache_id', '0')
        if {'entity_pid', 'entity_type'} <= columns('artwork_token'):
            fields['artwork'] = ('exists(select 1 from artwork_token art where '
                                 'art.entity_pid=i.item_pid and art.entity_type=0)')
            if {'artwork_token', 'artwork_source_type'} <= columns('artwork_token'):
                fields['artwork_id'] = ('coalesce((select art.artwork_token from artwork_token art where '
                    'art.entity_pid=i.item_pid and art.entity_type=0 and art.artwork_source_type=300 limit 1),0)')
        where = 'where (i.media_type & 8)<>0' if 'media_type' in schema_columns['item'] else ''
        if backend == 'iphone' and {'item_pid', 'is_library_added'} <= columns('item_state'):
            # Music may create a completely empty, unregistered scratch item
            # during playback/sync. It is not a library song. Keep untitled
            # audio, unfinished sync IDs and actual cloud library members.
            where += (' and (' if where else 'where (') + "coalesce(e.title,'')<>'' or coalesce(e.file_size,0)>0 or " + field('s','sync_id','0') + '<>0 or exists(select 1 from item_state st where st.item_pid=i.item_pid and st.is_library_added<>0))'
        sql = 'select ' + ','.join(f'{value} as {key}' for key, value in fields.items())
        sql += ' from item i join item_extra e on e.item_pid=i.item_pid ' + ' '.join(joins) + f' {where} limit {MAX_TRACKS + 1}'
        raw = db.execute(sql).fetchall()
        if len(raw) > MAX_TRACKS:
            raise LibraryError('library_too_large', 'Library exceeds the current track limit.')
        tracks = []
        for row in raw:
            r = dict(row)
            path = asset_path(backend, r.pop('base') or '', r.pop('location') or '')
            r['id'], r['wire_id'], r['album_wire_id'] = pid(r['id']), pid(r['wire_id']), pid(r['album_wire_id'])
            r['artwork_id'] = str(r['artwork_id'])
            r['title'], r['artist'], r['album'] = r['title'] or '', r['artist'] or '', r['album'] or ''
            r['album_artist'] = r['album_artist'] or r['artist']
            for key in ('size_bytes', 'duration_ms', 'year', 'track_number', 'disc_number'):
                r[key] = max(0, int(r[key] or 0))
            def group_id(kind, number, name):
                return kind + ':' + (pid(number) if number else hashlib.sha256(name.casefold().encode()).hexdigest()[:24])
            r['album_id'] = group_id('album', r.pop('album_pid'), r['album_artist'] + '\0' + r['album'])
            r['artist_id'] = group_id('artist', r.pop('artist_pid'), r['artist'])
            r['availability'] = 'local' if path and r['size_bytes'] else 'pending' if r['wire_id'] != '0' else 'cloud'
            r['asset_path'], r['has_artwork'] = path, bool(r.pop('artwork'))
            r['origin'] = 'device' if backend == 'touch' else 'synced' if r['wire_id'] != '0' else 'cloud'
            r['can_export'] = bool(r['availability'] == 'local' and Path(path).suffix.lower() != '.m4p'
                                   and r['origin'] != 'cloud')
            r['can_plan_delete'] = (r['availability'] == 'local' and Path(path).suffix.lower() != '.m4p'
                                    and (backend == 'touch' or r['wire_id'] != '0'))
            tracks.append(r)
        tracks.sort(key=lambda t: int(t['id']))
        if len({t['id'] for t in tracks}) != len(tracks):
            raise LibraryError('invalid_catalogue', 'Duplicate track identity in the catalogue.')
        membership_known = {'container_pid', 'item_pid', 'position'} <= schema_columns['container_item']
        members = {}
        if membership_known:
            rows = db.execute(f'select container_pid,item_pid,position from container_item order by container_pid,position,container_item_pid limit {MAX_MEMBERS + 1}').fetchall()
            if len(rows) > MAX_MEMBERS:
                raise LibraryError('library_too_large', 'Playlist membership exceeds the current limit.')
            for row in rows:
                members.setdefault(pid(row['container_pid']), []).append(pid(row['item_pid']))
        playlists = []
        if {'container_pid', 'name'} <= schema_columns['container']:
            raw_playlists = db.execute(f'select * from container order by container_pid limit {MAX_PLAYLISTS + 1}').fetchall()
            if len(raw_playlists) > MAX_PLAYLISTS:
                raise LibraryError('library_too_large', 'Library exceeds the current playlist limit.')
            for row in raw_playlists:
                r = dict(row)
                identifier = pid(r['container_pid'])
                flags = ('distinguished_kind', 'container_type', 'is_hidden', 'parent_pid', 'smart_is_folder',
                         'smart_is_dynamic', 'smart_is_filtered', 'smart_is_genius', 'is_external_vendor_playlist',
                         'is_collaborative', 'cloud_is_subscribed', 'store_cloud_id')
                known = set(flags) | {'sync_id', 'is_src_remote', 'cloud_global_id', 'smart_criteria'}
                ordinary = (backend == 'iphone' and known <= set(r) and identifier != pid(info['primary_container_pid'])
                            and identifier != '0' and pid(r.get('sync_id', 0)) == identifier
                            and r.get('is_src_remote') == 1 and not r.get('cloud_global_id')
                            and r.get('smart_criteria') is None and not any(r.get(k, 0) for k in flags))
                ids = members.get(identifier, []) if membership_known else None
                playlists.append({'id': identifier, 'wire_id': pid(r.get('sync_id', 0)), 'name': r['name'] or '',
                                  'kind': 'library' if identifier == pid(info['primary_container_pid']) else
                                          'folder' if r.get('smart_is_folder') else 'smart' if any(r.get(k) for k in
                                          ('smart_is_dynamic', 'smart_is_filtered', 'smart_is_genius')) else
                                          'cloud' if r.get('cloud_global_id') or r.get('store_cloud_id') else 'playlist',
                                  'is_hidden': bool(r.get('is_hidden')), 'track_ids': ids,
                                  'track_count': len(ids) if ids is not None else None, 'can_plan_delete': ordinary})
        native = dict(native or {})
        native.update(db_pid=pid(info['db_pid']), primary_container_pid=pid(info['primary_container_pid']))
        if backend == 'touch' and {'genre_id', 'genre'} <= schema_columns['genre']:
            native['genres'] = [{'id': pid(r[0]), 'name': r[1] or ''}
                               for r in db.execute('select genre_id,genre from genre order by genre_id')]
        if backend == 'iphone' and 'anchor' not in native:
            anchor = db.execute("select value from _MLDatabaseProperties where key='MLSyncClientSyncAnchor'").fetchone()
            native['anchor'] = int(anchor[0]) if anchor else None
        occupied = {native['db_pid'], native['primary_container_pid']}
        for table in ('item_store', 'album', 'album_artist', 'composer', 'container'):
            if 'sync_id' in columns(table):
                occupied.update(pid(r[0]) for r in db.execute('select sync_id from ' + table))
        native['occupied_wire_ids'] = sorted(occupied, key=int)
        result = {'snapshot_id': uuid.uuid4().hex, 'backend': backend, 'device_id': device_id,
                  'library_id': native['db_pid'], 'captured_at': datetime.now(timezone.utc).isoformat(),
                  'schema': schema, 'tracks': tracks, 'playlists': playlists, 'native': native,
                  'capabilities': capabilities(backend, membership_known)}
        return seal(result)
