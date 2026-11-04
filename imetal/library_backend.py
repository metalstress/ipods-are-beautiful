"""Headless album/track API for the future UI; no Qt or visible controls."""
import asyncio
import hashlib
import json
from pathlib import Path
import re

from library_devices import device_identity, read_device
from library_model import LibraryError, query, seal, summary
from library_plans import plan
from transport import DATA
from device_lock import device_lock


def plan_hash(record):
    return hashlib.sha256(json.dumps({k:v for k,v in record.items() if k != 'content_hash'},
        sort_keys=True, ensure_ascii=True, separators=(',', ':')).encode()).hexdigest()


class LibraryCache:
    def __init__(self, root=None):
        self.root = Path(root) if root is not None else DATA / 'library-cache'

    def save(self, kind, record):
        key = 'snapshot_id' if kind == 'snapshots' else 'plan_id'
        identifier = record[key]
        if kind == 'plans':
            record['content_hash'] = plan_hash(record)
        path = self._path(kind, identifier)
        path.parent.mkdir(parents=True, exist_ok=True)
        # UUID names are new; exclusive create avoids concurrent readers seeing
        # an older snapshot replaced behind their selection.
        with path.open('x', encoding='utf-8') as target:
            json.dump(record, target, ensure_ascii=False)
        return record

    def _path(self, kind, identifier):
        if kind not in ('snapshots', 'plans') or not isinstance(identifier, str) or not re.fullmatch('[0-9a-f]{32}', identifier):
            raise LibraryError('invalid_cache_id', 'Invalid library snapshot or plan identifier.')
        return self.root / kind / (identifier + '.json')

    def load(self, kind, identifier):
        path = self._path(kind, identifier)
        try:
            with path.open(encoding='utf-8') as source:
                record = json.load(source)
        except FileNotFoundError as error:
            raise LibraryError('snapshot_expired', 'Library snapshot is missing. Refresh the library.') from error
        except (OSError, ValueError) as error:
            raise LibraryError('cache_unreadable', 'Library cache could not be read. Refresh the library.') from error
        if kind == 'snapshots':
            try:
                expected = record['revision']
                if record['snapshot_id'] != identifier or seal(record)['revision'] != expected:
                    raise ValueError('snapshot contents differ')
            except (KeyError, TypeError, ValueError) as error:
                raise LibraryError('cache_unreadable', 'Library cache contents changed. Refresh the library.') from error
        elif not isinstance(record, dict) or record.get('plan_id') != identifier or record.get('content_hash') != plan_hash(record):
            raise LibraryError('invalid_plan', 'The prepared change was modified. Prepare it again.')
        return record


class LibraryService:
    def __init__(self, cache=None):
        self.cache = cache or LibraryCache()

    def execute(self, command, config, payload, emit=lambda e: None, stop=None):
        if not isinstance(payload, dict):
            raise LibraryError('invalid_request', 'Library request must be an object.')
        if command == 'library.read':
            with device_lock(config):
                record = self.cache.save('snapshots', asyncio.run(asyncio.wait_for(read_device(config, stop), 120)))
            return summary(record)
        if command == 'library.apply':
            return self.apply(config, payload, emit, stop)
        snapshot = self.cache.load('snapshots', payload.get('snapshot_id'))
        _, identity = device_identity(config)
        if identity != snapshot['device_id']:
            raise LibraryError('wrong_device', 'This snapshot belongs to another selected device.')
        if command == 'library.query':
            return query(snapshot, payload)
        if command == 'library.plan':
            proposal = self.cache.save('plans', plan(snapshot, payload.get('action'), payload))
            private = {'native_payload', 'cover', 'artwork_ids', 'content_hash'}
            return {k: v for k, v in proposal.items() if k not in private}
        if command == 'library.artwork':
            from library_covers import read_cover
            with device_lock(config):
                return asyncio.run(asyncio.wait_for(read_cover(config, snapshot, payload.get('track_id'), stop), 120))
        raise LibraryError('unsupported_operation', 'Unknown library command.')

    def apply(self, config, payload, emit, stop):
        with device_lock(config):
            return self._apply(config, payload, emit, stop)

    def _apply(self, config, payload, emit, stop):
        from library_mutations import apply_device
        from library_model import capabilities
        proposal = self.cache.load('plans', payload.get('plan_id'))
        snapshot = self.cache.load('snapshots', proposal['snapshot_id'])
        backend, identity = device_identity(config)
        if identity != proposal['device_id']:
            raise LibraryError('wrong_device', 'This change belongs to another device.')
        capability = {'delete_tracks': 'delete_tracks', 'update_metadata': 'edit_metadata',
                      'replace_artwork': 'replace_artwork'}.get(proposal['action'])
        if not capability or not capabilities(backend, False)[capability]['enabled']:
            raise LibraryError('writes_disabled', 'This library change has not passed device validation.')
        path = self.cache.root / 'runs' / (proposal['plan_id'] + '.json')
        path.parent.mkdir(parents=True, exist_ok=True)
        # One-use receipt is durable BEFORE transport/submission. A lost response
        # must lead to a refresh, never a blind retry of a possibly committed sync.
        try:
            with path.open('x', encoding='utf-8') as receipt:
                json.dump({'state': 'started', 'plan_id': proposal['plan_id']}, receipt)
        except FileExistsError as error:
            raise LibraryError('plan_already_submitted', 'This change was already submitted. Refresh the library.') from error
        try:
            record = self.cache.save('snapshots', asyncio.run(apply_device(config, snapshot, proposal, emit, stop)))
        except Exception as error:
            path.write_text(json.dumps({'state': 'refresh_required', 'plan_id': proposal['plan_id']}), encoding='utf-8')
            raise LibraryError('change_not_verified', str(error) or type(error).__name__,
                cause=getattr(error, 'code', None), refresh_required=True) from error
        response = summary(record) | {'action': proposal['action'], 'changed': proposal['track_count'], 'verified': True}
        path.write_text(json.dumps({'state': 'verified', 'snapshot_id': record['snapshot_id']}), encoding='utf-8')
        return response
