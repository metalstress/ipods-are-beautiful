from pathlib import Path
from contextlib import closing
import tempfile
import threading
import sqlite3
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from modern_engine import ModernEngine
from transfer_speed import TransferSpeed


class CatalogueArtworkTests(unittest.TestCase):
    def test_cloud_artwork_hashes_do_not_break_import_catalogue(self):
        from modern_music import read_catalogue
        from test_library import fixture
        with tempfile.TemporaryDirectory() as folder:
            database = Path(folder) / 'catalogue.db'
            fixture(database)
            with closing(sqlite3.connect(database)) as db, db:
                db.executescript('''
                    CREATE TABLE composer(sync_id INTEGER);
                    CREATE TABLE artwork(artwork_token TEXT,artwork_source_type INTEGER);
                    CREATE TABLE artwork_token(artwork_token TEXT,artwork_source_type INTEGER);
                    INSERT INTO artwork VALUES('100',300),('1603630449',300);
                    INSERT INTO artwork_token VALUES('101',300);
                ''')
                db.executemany('INSERT INTO artwork_token VALUES(?,300)', [
                    ('193d975e21827454e6569620bfb6f496bc8afeb98f542bb8d8e72b6723e77d8f',),
                    ('1' * 64,), (None,),
                ])
            self.assertEqual(read_catalogue(database)['artwork_ids'], {100, 101, 1603630449})


class Device:
    imports = 0
    fail = False
    stop = None
    known = False
    async def __aenter__(self):return self
    async def __aexit__(self,*args):pass
    def __init__(self,udid,root,emit,**kwargs):
        self.emit=emit
        self.afc=self
    async def snapshot(self,*args):return {}
    async def already_present(self,track):return {'skipped':True} if self.known else None
    async def get_device_info(self):return {'FSFreeBytes':10**12,'FSTotalBytes':2*10**12}
    async def push_alac(self,track):
        if self.fail:raise ConnectionError('Lost connection after metadata')
        self.emit({'stage':'Uploading','bytes_transferred':track.path.stat().st_size})
        if self.stop:self.stop.set()
        self.imports+=1
        return {'state':'verified'}


class ModernQueueTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name).resolve()
        self.a=self.root/'a.flac';self.a.write_bytes(b'a'*10)
        self.b=self.root/'b.flac';self.b.write_bytes(b'b'*20)
        self.events=[]
        Device.imports=0;Device.fail=False;Device.stop=None;Device.known=False
        self.patches=[patch('modern_engine.ModernDevice',Device),
            patch('modern_engine.album_artist_overrides',return_value={}),
            patch('modern_engine.complete_artwork'),
            patch('modern_engine.DATA',self.root),
            patch('modern_engine.remember_capacity'),
            patch('modern_engine.inspect',lambda p:SimpleNamespace(title=p.stem,format='FLAC')),
            patch('modern_engine.native_format',return_value=None),
            patch('modern_engine.prepare_audio',lambda p,*a:SimpleNamespace(path=p,format='ALAC'))]
        for p in self.patches:p.start()
        self.config={'udid':'test-only','backup_root':str(self.root),'host':None}
    def tearDown(self):
        for p in reversed(self.patches):p.stop()
        self.tmp.cleanup()
    def test_counts_actual_confirmed_imports_and_payload_bytes(self):
        r=ModernEngine(self.config,self.events.append).transfer([self.a,self.a,self.b])
        self.assertEqual((r['tracks_total'],r['imported'],r['bytes_transferred'],r['bytes_total']),(2,2,30,30))
        self.assertFalse(r['bytes_estimated'])
        self.assertEqual(len([e for e in self.events if e.get('state')=='On iPhone']),2)
    def test_skip_is_not_reported_as_uploaded_or_imported(self):
        Device.known=True
        r=ModernEngine(self.config,self.events.append).transfer([self.a])
        self.assertEqual((r['skipped'],r['imported'],r['bytes_transferred'],r['bytes_total']),(1,0,0,0))

    def test_batch_album_artist_overrides_cached_conversion_tags(self):
        def source(path):
            return SimpleNamespace(title=path.stem, format='FLAC', album_artist='Individual artist',
                                   release_ids=(('barcode','00194491369125'),), cover=b'')
        with patch('modern_engine.inspect',side_effect=source), \
                patch('modern_engine.album_artist_overrides',return_value={str(self.a):'Various Artists'}), \
                patch.object(Device,'push_alac',new_callable=AsyncMock) as upload:
            result=ModernEngine(self.config,self.events.append).transfer([self.a])
        ready=upload.await_args.args[0]
        self.assertEqual(ready.album_artist,'Various Artists')
        self.assertEqual(ready.release_ids,(('barcode','00194491369125'),))
        self.assertTrue(ready.compilation)
        self.assertEqual(result['imported'],1)
    def test_eject_finishes_current_track_and_stops_next(self):
        stop=threading.Event();Device.stop=stop
        r=ModernEngine(self.config,self.events.append,stop).transfer([self.a,self.b])
        self.assertTrue(r['cancelled'])
        self.assertEqual((r['imported'],r['bytes_transferred']),(1,10))
    def test_failed_native_session_is_closed_and_next_track_reads_a_fresh_catalogue(self):
        with patch.object(Device, 'push_alac', new_callable=AsyncMock,
                          side_effect=[ConnectionError('metadata sent'), {'state': 'verified'}]) as upload, \
                patch.object(Device, 'snapshot', new_callable=AsyncMock, return_value={}) as snapshot, \
                patch.object(Device, '__aexit__', new_callable=AsyncMock, return_value=None) as close:
            r=ModernEngine(self.config,self.events.append).transfer([self.a,self.b])
        self.assertEqual([call.args[0].path for call in upload.await_args_list], [self.a, self.b])
        self.assertEqual(snapshot.await_count, 2)
        self.assertEqual(close.await_count, 2)
        self.assertEqual((r['imported'], r['errors']), (1, 1))
        self.assertNotIn('interrupted', r)
        states=[(e['path'],e['state']) for e in self.events if e.get('state') in ('Failed','On iPhone')]
        self.assertEqual(states,[(str(self.a),'Failed'),(str(self.b),'On iPhone')])

    def test_bad_source_is_skipped_and_final_counts_exclude_its_unused_bytes(self):
        def prepare(path, *args):
            if path == self.a:
                raise ValueError('Invalid audio')
            return SimpleNamespace(path=path, format='ALAC')
        with patch('modern_engine.prepare_audio', side_effect=prepare):
            result=ModernEngine(self.config,self.events.append).transfer([self.a,self.b])
        self.assertEqual((result['errors'],result['imported'],result['bytes_transferred'],result['bytes_total']),
                         (1,1,20,20))
        self.assertFalse(result['bytes_estimated'])

    def test_missing_source_is_skipped_and_does_not_abort_the_batch(self):
        self.a.unlink()
        result=ModernEngine(self.config,self.events.append).transfer([self.a,self.b])
        self.assertEqual((result['errors'],result['imported']), (1,1))

    def test_all_failed_tracks_are_attempted_once(self):
        with patch.object(Device,'push_alac',side_effect=ConnectionError('metadata sent')) as upload:
            result=ModernEngine(self.config,self.events.append).transfer([self.a,self.b])
        self.assertEqual(upload.call_count,2)
        self.assertEqual((result['errors'],result['imported']), (2,0))
        self.assertNotIn('interrupted',result)

    def test_partial_upload_bytes_are_preserved_when_the_queue_continues(self):
        async def upload(device, track):
            count = 5 if track.path == self.a else track.path.stat().st_size
            device.emit({'stage':'Uploading','bytes_transferred':count})
            if track.path == self.a:
                raise ConnectionError('Failed after partial upload')
            return {'state':'verified'}
        with patch.object(Device,'push_alac',upload):
            result=ModernEngine(self.config,self.events.append).transfer([self.a,self.b])
        self.assertEqual((result['bytes_transferred'],result['bytes_total']),(25,25))
        totals=[event['bytes_transferred'] for event in self.events if 'bytes_transferred' in event]
        self.assertEqual(totals,sorted(totals))


class SpeedTests(unittest.TestCase):
    def test_speed_uses_payload_bytes_and_resets_between_stages(self):
        now=[10.0];meter=TransferSpeed(lambda:now[0])
        meter.update(0,False)
        now[0]=11
        self.assertEqual(meter.update(2*1024**2),2*1024**2)
        now[0]=12
        self.assertEqual(meter.update(4*1024**2),2*1024**2)
        now[0]=13
        self.assertEqual(meter.update(4*1024**2,False),0)
        now[0]=14
        self.assertEqual(meter.update(5*1024**2),1024**2)


if __name__=='__main__':unittest.main()
