"""Queue adapter for the verified append-only native iPhone transport."""
import asyncio
from pathlib import Path
import threading

from media import Cancelled, inspect
from modern_devices import pair_record,remember_capacity
from modern_music import ModernDevice, prepare_audio, native_format
from transport import DATA
from datetime import datetime


class ModernEngine:
    def __init__(self, config, emit=lambda e: None, stop=None):
        self.config, self.emit = config, emit
        self.stop = stop if stop is not None else threading.Event()

    def transfer(self, paths):
        return asyncio.run(self.run(paths))

    async def run(self, paths):
        paths = list({str(Path(p).resolve()).casefold(): Path(p).resolve() for p in paths}.values())
        if not paths:
            return {'tracks_total': 0, 'imported': 0, 'skipped': 0, 'errors': 0,
                    'bytes_transferred': 0, 'bytes_total': 0, 'bytes_estimated': False}
        root = Path(self.config['backup_root'])
        sizes = {str(p): p.stat().st_size for p in paths}
        estimated = set(sizes)
        imported = skipped = errors = transferred = 0
        current = 0
        record = await pair_record(self.config['udid']) if self.config.get('host') else None

        def counters():
            return {'tracks_total': len(paths), 'imported': imported, 'skipped': skipped,
                    'errors': errors, 'bytes_transferred': transferred + current,
                    'bytes_total': sum(sizes.values()), 'bytes_estimated': bool(estimated)}

        for index, path in enumerate(paths):
            if self.stop.is_set():
                return {**counters(), 'cancelled': True}
            track = None
            format_label = ''
            current = 0

            def event(fraction, stage, state=None, detail=None):
                payload = {'path': str(path), 'overall': (index + fraction) / len(paths),
                           'title': track.title if track else path.stem, 'stage': stage,
                           **counters()}
                if state:
                    payload['state'] = state
                if detail:
                    payload['detail'] = detail
                if track:
                    payload['format'] = format_label or track.format
                self.emit(payload)

            try:
                event(0, 'Reading metadata', 'Preparing')
                track = await asyncio.to_thread(inspect, path)
                native = native_format(track)
                event(.02, 'Preparing' if native else 'Converting to ALAC',
                      'Preparing' if native else 'Converting')
                ready = await asyncio.to_thread(prepare_audio, path, root / 'conversion-cache', self.stop,
                                               lambda value: event(.02 + .26 * value, 'Converting to ALAC', 'Converting'))
                format_label = track.format if ready.path == path else track.format + ' → ' + ready.format
                sizes[str(path)] = ready.path.stat().st_size
                estimated.discard(str(path))
                if self.stop.is_set():
                    raise Cancelled()

                def progress(message):
                    nonlocal current
                    current = message.get('bytes_transferred', current)
                    stage = message.get('stage', 'Uploading')
                    value = .36 + .44 * current / max(sizes[str(path)], 1) if stage == 'Uploading' else .9
                    event(value, stage, stage)

                async with ModernDevice(self.config['udid'], root, progress,
                                        host=self.config.get('host'), pair_record=record) as device:
                    event(.3, 'Reading Music library', 'Preparing')
                    await asyncio.wait_for(device.snapshot('before'), 60)
                    known = await device.already_present(ready)
                    if known:
                        skipped += 1
                        sizes[str(path)] = 0
                        event(1, 'Already on iPhone', 'Already on iPhone')
                        continue
                    if self.stop.is_set():
                        raise Cancelled()
                    capacity = await device.afc.get_device_info()
                    remember_capacity(self.config['udid'],int(capacity['FSFreeBytes']),int(capacity['FSTotalBytes']))
                    if int(capacity['FSFreeBytes']) < sizes[str(path)] + 16 * 1024**2:
                        raise RuntimeError('Not enough free space on iPhone.')
                    event(.35, 'Uploading', 'Uploading')
                    # Finish a submitted native operation before honoring Eject.
                    # An interrupted/ambiguous native session stops the batch;
                    # it is never automatically repeated.
                    await asyncio.wait_for(device.push_alac(ready), 600)
                    capacity = await device.afc.get_device_info()
                    remember_capacity(self.config['udid'],int(capacity['FSFreeBytes']),int(capacity['FSTotalBytes']))
                    self.emit({'type': 'connection', 'free': int(capacity['FSFreeBytes']),
                               'total': int(capacity['FSTotalBytes'])})
                transferred += current
                current = 0
                imported += 1
                event(1, 'Imported into Music', 'On iPhone')
            except Cancelled:
                event(0, 'Stopped', 'Stopped')
                return {**counters(), 'cancelled': True}
            except Exception as error:
                errors += 1
                detail=('iPhone is not responding. Unlock it and check USB or Wi-Fi.'
                        if isinstance(error,TimeoutError) else str(error) or type(error).__name__)
                event(1, 'Import failed', 'Failed', detail)
                DATA.mkdir(parents=True,exist_ok=True)
                with (DATA/'errors.log').open('a',encoding='utf-8') as log:
                    log.write(f'{datetime.now().isoformat()} {path.name}: {detail}\n')
                # The phone may have committed metadata before a link failure.
                # Do not start further sync sessions on that uncertain state.
                return {**counters(), 'interrupted': True}
        return {**counters(), 'cancelled': self.stop.is_set()}
