"""Native metadata/cover sessions; uses the same signed ATC receiver as uploads.

Protocol: rustunes 057ee8fc, docs/02 and docs/05 (MIT). No device SQL writes.
"""
import asyncio
import json
import plistlib
import struct

from library_model import LibraryError, pid
from modern_music import OPERATION, SIGNATURE, STAGING, native_sign


def classify_manifest(manifest, owned, occupied):
    if not isinstance(manifest, list) or len(manifest) > 1024:
        raise LibraryError('unexpected_manifest', 'Invalid Music asset manifest.')
    covers, ancillary, seen = [], [], set()
    for asset in manifest:
        aid = asset.get('AssetID') if isinstance(asset, dict) else None
        try:
            identity = pid(aid)
        except LibraryError as error:
            raise LibraryError('unexpected_manifest', 'Music requested an unidentified asset.') from error
        parts = asset.get('Variant', {}).get('AssetParts')
        if (identity in seen or asset.get('AssetType') != 'Music'
                or asset.get('IsDownload') is not True or type(parts) is not int):
            raise LibraryError('unexpected_manifest', 'Unexpected Music asset request.')
        seen.add(identity)
        if identity in owned and parts in (2, 6):
            covers.append((aid, identity))
        elif identity not in owned and identity != '0' and identity in occupied and parts in (2, 4):
            ancillary.append(aid)
        else:
            raise LibraryError('unexpected_manifest', 'Music requested audio or an unsupported existing asset.')
    if {identity for _, identity in covers} != set(owned):
        raise LibraryError('unexpected_manifest', 'Music did not request every selected cover.')
    return covers, ancillary


async def submit(device, operation, covers=None, emit=lambda e: None):
    covers = covers or {}
    before = device.catalogue
    journal = device.backup
    (journal / 'library-operation.plist').write_bytes(operation)
    report = {'state': 'prepared', 'cover_ids': list(covers), 'frames': device.frames}
    def save(state):
        report['state'] = state
        (journal / 'library-operation.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    save('prepared')
    await device.ready()
    signature = native_sign('sign', struct.pack('<I', len(operation)) + device.reply + operation)
    if len(signature) != 21:
        raise LibraryError('invalid_signature', 'Unexpected Music signature size.')
    await device.afc.makedirs(STAGING)
    await device.staging_empty()
    save('staging')
    await device.write_new(OPERATION, operation)
    await device.write_new(SIGNATURE, signature)
    # A metadata-only receiver may go straight to SyncFinished. An artwork
    # receiver must still be rejected if it finishes before the owned assets.
    device.completion_sent = not covers
    await device.send('FinishedSyncingMetadata', {
        'DataclassAnchors': {'Media': str(before['anchor'] + 1), 'VoiceMemo': '1'},
        'SyncTypes': {'Media': 1, 'Book': 0, 'Keybag': 1}})
    save('metadata-sent')
    manifest_seen = False
    for _ in range(256):
        message = await device.receive(timeout=60)
        if message.get('Session') != 2:
            raise LibraryError('unexpected_session', 'Music session identity changed.')
        command = message['Command']
        if command == 'AssetManifest':
            if manifest_seen:
                raise LibraryError('unexpected_manifest', 'Music sent an additional asset manifest.')
            manifest_seen = True
            manifest = message.get('Params', {}).get('AssetManifest', {}).get('Media')
            owned, ancillary = classify_manifest(manifest, covers,
                {pid(i) for i in before['occupied']})
            # Validate the entire manifest before sending any completion/refusal.
            await device.afc.makedirs('/Airlock/Media/Artwork')
            for index, (aid, identity) in enumerate(owned):
                await device.write_new('/Airlock/Media/Artwork/' + aid, covers[identity])
                device.check_atc()
                if index == len(owned) - 1:
                    device.completion_sent = True
                await device.send('FileComplete', {'AssetID': aid, 'Dataclass': 'Media', 'AssetPath': ''})
                emit({'type': 'library_change', 'stage': 'Updating artwork',
                      'completed': index + 1, 'total': len(owned)})
            for aid in ancillary:
                await device.send('FileError', {'AssetID': aid, 'Dataclass': 'Media',
                                              'ErrorCode': 16, 'ErrorIsVisible': False})
        elif command == 'SyncFinished':
            if covers and not manifest_seen:
                raise LibraryError('missing_artwork', 'Music finished without receiving the new cover.')
            break
        elif command not in ('Progress', 'SyncAllowed', 'InstalledAssets', 'AssetMetrics'):
            raise LibraryError('unexpected_response', 'Unexpected Music response: ' + command)
    else:
        raise LibraryError('sync_timeout', 'Music did not finish its metadata session.')
    save('native-finished')
    await device.stop_reader()
    await device.atc.close()
    device.atc = None
    emit({'type': 'library_change', 'stage': 'Verifying library'})
    await asyncio.sleep(.5)
    await device.snapshot('library-after')
    if device.catalogue['anchor'] != before['anchor'] + 1:
        raise LibraryError('verification_failed', 'Music revision did not advance as expected.')
    return journal
