import asyncio
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock, patch

from PIL import Image
from device_lock import device_lock
from library_artwork import cover_bytes, prepare_cover
from library_backend import LibraryCache, LibraryService
from library_catalogue import read_snapshot
from library_devices import device_identity
from library_model import LibraryError
from library_modern import classify_manifest, submit
from library_mutations import verify_change
from library_plans import plan
from library_touch import selection_script
from test_library import fixture


def asset(identity, parts):
    return {'AssetID': identity, 'AssetType': 'Music', 'IsDownload': True, 'Variant': {'AssetParts': parts}}


class MutationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.config = {'backend': 'modern', 'udid': 'fixture'}
        db = self.root/'db';fixture(db)
        self.snapshot = read_snapshot(db, 'iphone', device_identity(self.config)[1])

    def tearDown(self):
        self.temp.cleanup()

    def test_manifest_refuses_foreign_audio_duplicates_and_missing_owned_cover(self):
        valid = classify_manifest([asset('-2', 6), asset('212', 4)], {str(2**64-2)}, {'212'})
        self.assertEqual(valid, ([('-2',str(2**64-2))], ['212']))
        for data in ([asset('-2',3)], [asset('-2',2),asset('212',1)],
                     [asset('-2',2),asset('-2',2)], [asset('212',4)]):
            with self.subTest(data=data), self.assertRaises(LibraryError):
                classify_manifest(data, {str(2**64-2)}, {'212'})

    def test_album_delete_plan_uses_all_and_only_album_members(self):
        snapshot=copy.deepcopy(self.snapshot)
        snapshot['tracks']=snapshot['tracks'][:2]
        proposal=plan(snapshot,'delete_tracks',{'album_ids':[snapshot['tracks'][0]['album_id']]})
        self.assertEqual(proposal['track_ids'],['11','12'])
        after=copy.deepcopy(snapshot);after['tracks']=[]
        verify_change(snapshot,after,proposal)
        after['tracks']=snapshot['tracks'][:1]
        with self.assertRaises(LibraryError):verify_change(snapshot,after,proposal)

    def test_verification_rejects_unrelated_metadata_changes(self):
        proposal=plan(self.snapshot,'update_metadata',{'track_ids':['11'],'changes':{'genre':'New','year':2025}})
        after=copy.deepcopy(self.snapshot);after['tracks'][0].update(genre='New',year=2025)
        verify_change(self.snapshot,after,proposal)
        after['tracks'][1]['genre']='New'
        with self.assertRaises(LibraryError):verify_change(self.snapshot,after,proposal)

    def test_normalized_cover_is_immutable_rgb_and_allocations_do_not_collide(self):
        source=self.root/'cover.png';Image.new('RGBA',(1200,600),(25,150,70,128)).save(source)
        with patch('library_artwork.DATA',self.root):
            proposal=plan(self.snapshot,'replace_artwork',{'track_ids':['11','12'],'cover_path':str(source)})
        self.assertEqual(len(set(proposal['artwork_ids'].values())),2)
        data=cover_bytes(proposal['cover'])
        with Image.open(proposal['cover']['path']) as image:
            self.assertEqual(image.mode,'RGB');self.assertEqual(image.size,(1024,512))
        Path(proposal['cover']['path']).write_bytes(data+b'changed')
        with self.assertRaises(LibraryError):cover_bytes(proposal['cover'])

    def test_exact_legacy_ids_and_new_genres_never_replace_an_existing_collection(self):
        from library_touch import apply
        tracks=[self.snapshot['tracks'][0]]
        proposal={'action':'update_metadata','changes':{'genre':'Hip-Hop'}}
        session=Mock();bridge=Mock();bridge.execute.return_value={'submitted':1}
        with patch('library_touch.bridge_for',return_value=bridge):
            apply(session,tracks,proposal,genres=[{'id':'9007199254740993','name':'Hip-Hop'}])
        script=bridge.execute.call_args.args[0]
        self.assertIn('9007199254740993',script)
        self.assertNotIn('newWithDictionary:',script)
        self.assertIn('NSJSONSerialization JSONObjectWithData:',script)
        self.assertIn('getBytes:argument length:8',script)
        self.assertIn('unsignedLongLongValue',script)
        self.assertIn('isEqualToData:identityData',script)
        self.assertIn('containsObject:identity',selection_script(tracks))
        with patch('library_touch.bridge_for',return_value=bridge):
            apply(session,tracks,proposal,genres=[{'id':str(2**64-2),'name':'Hip-Hop'}])
        self.assertIn('@"[-2]"',bridge.execute.call_args.args[0])
        self.assertNotIn(str(2**64-2),bridge.execute.call_args.args[0])

    def test_empty_unregistered_phone_scratch_item_is_not_a_library_song(self):
        import sqlite3
        from contextlib import closing
        db=self.root/'scratch.db';fixture(db)
        with closing(sqlite3.connect(db)) as conn,conn:
            conn.executescript('''
                CREATE TABLE item_state(item_pid INTEGER,is_library_added INTEGER);
                INSERT INTO item VALUES(16,8,0,0,0,0,0,0,0);
                INSERT INTO item_extra VALUES(16,'','',0,0,0,0);
                INSERT INTO item_store VALUES(16,0,'');
                INSERT INTO item_state VALUES(16,0);
            ''')
        data=read_snapshot(db,'iphone',self.snapshot['device_id'])
        self.assertNotIn('16',[t['id'] for t in data['tracks']])

    def test_changed_plan_and_replayed_plan_fail_before_device(self):
        cache=LibraryCache(self.root/'cache');cache.save('snapshots',self.snapshot)
        proposal=cache.save('plans',plan(self.snapshot,'delete_tracks',{'track_ids':['11']}))
        service=LibraryService(cache)
        caps={'delete_tracks':{'enabled':True}}
        after=copy.deepcopy(self.snapshot);after['snapshot_id']='a'*32
        after['tracks']=after['tracks'][1:]
        from library_model import seal
        seal(after)
        with patch('library_model.capabilities',return_value=caps),patch('device_lock.DATA',self.root),patch('library_mutations.apply_device',AsyncMock(return_value=after)) as operation:
            self.assertTrue(service.execute('library.apply',self.config,{'plan_id':proposal['plan_id']})['verified'])
            with self.assertRaisesRegex(LibraryError,'already submitted'):
                service.execute('library.apply',self.config,{'plan_id':proposal['plan_id']})
            self.assertEqual(operation.await_count,1)
        path=cache._path('plans',proposal['plan_id']);record=json.loads(path.read_text(encoding='utf-8'))
        record['track_ids']=['12'];path.write_text(json.dumps(record),encoding='utf-8')
        with self.assertRaisesRegex(LibraryError,'modified'):cache.load('plans',proposal['plan_id'])

    def test_device_lock_prevents_overlapping_operations_and_releases_after_failure(self):
        with patch('device_lock.DATA',self.root):
            with device_lock(self.config):
                with self.assertRaisesRegex(LibraryError,'Another operation'):
                    with device_lock(self.config):pass
            with device_lock(self.config):pass


class NativeSessionTests(unittest.IsolatedAsyncioTestCase):
    async def test_metadata_only_accepts_direct_finished_and_never_sends_audio_completion(self):
        with tempfile.TemporaryDirectory() as root:
            device=Mock();device.backup=Path(root);device.catalogue={'anchor':9,'occupied':set()}
            device.frames=[];device.reply=b'x'*83;device.afc=AsyncMock()
            device.ready=AsyncMock();device.staging_empty=AsyncMock();device.write_new=AsyncMock()
            device.send=AsyncMock();device.receive=AsyncMock(return_value={'Session':2,'Command':'SyncFinished'})
            device.stop_reader=AsyncMock();device.atc=AsyncMock()
            async def capture(label):device.catalogue={'anchor':10}
            device.snapshot=capture
            with patch('library_modern.native_sign',return_value=b's'*21),patch('library_modern.asyncio.sleep',AsyncMock()):
                await submit(device,b'operation')
            self.assertTrue(device.completion_sent)
            self.assertEqual([c.args[0] for c in device.send.await_args_list],['FinishedSyncingMetadata'])

    async def test_bad_manifest_does_not_complete_or_refuse_any_assets(self):
        with tempfile.TemporaryDirectory() as root:
            device=Mock();device.backup=Path(root);device.catalogue={'anchor':9,'occupied':{212}}
            device.frames=[];device.reply=b'x'*83;device.afc=AsyncMock()
            device.ready=AsyncMock();device.staging_empty=AsyncMock();device.write_new=AsyncMock();device.send=AsyncMock()
            device.receive=AsyncMock(return_value={'Session':2,'Command':'AssetManifest','Params':{'AssetManifest':{'Media':[asset('-2',2),asset('212',1)]}}})
            with patch('library_modern.native_sign',return_value=b's'*21),self.assertRaises(LibraryError):
                await submit(device,b'operation',{str(2**64-2):b'cover'})
            self.assertEqual([c.args[0] for c in device.send.await_args_list],['FinishedSyncingMetadata'])


if __name__=='__main__':unittest.main()
