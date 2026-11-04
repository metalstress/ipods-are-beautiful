import asyncio
import base64
import copy
from contextlib import closing
import hashlib
import io
import json
import os
from pathlib import Path
import plistlib
import sqlite3
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from backend_client import run_backend
from library_backend import LibraryCache, LibraryService
from library_catalogue import read_snapshot
from library_devices import device_identity, export_device, _touch_capture
from library_export import ExportFile, filename
from library_model import LibraryError, asset_path, pid, query, seal, summary, wire_id
from library_plans import assert_fresh, plan


def fixture(path, backend='iphone'):
    with closing(sqlite3.connect(path)) as db, db:
        db.executescript('''
        CREATE TABLE db_info(db_pid INTEGER,primary_container_pid INTEGER);
        INSERT INTO db_info VALUES(-40,101);
        CREATE TABLE item(item_pid INTEGER PRIMARY KEY,media_type INTEGER,item_artist_pid INTEGER,
          album_pid INTEGER,album_artist_pid INTEGER,genre_id INTEGER,base_location_id INTEGER,
          track_number INTEGER,disc_number INTEGER);
        CREATE TABLE item_extra(item_pid INTEGER,title TEXT,location TEXT,file_size INTEGER,
          total_time_ms INTEGER,year INTEGER,artwork_cache_id INTEGER);
        CREATE TABLE item_artist(item_artist_pid INTEGER,item_artist TEXT);
        INSERT INTO item_artist VALUES(41,'Artist');
        CREATE TABLE album(album_pid INTEGER,album TEXT,album_artist_pid INTEGER,sync_id INTEGER);
        INSERT INTO album VALUES(21,'Album',31,401);
        CREATE TABLE album_artist(album_artist_pid INTEGER,album_artist TEXT,sync_id INTEGER);
        INSERT INTO album_artist VALUES(31,'Artist',501);
        CREATE TABLE genre(genre_id INTEGER,genre TEXT);
        INSERT INTO genre VALUES(1,'Doom');
        CREATE TABLE base_location(base_location_id INTEGER,path TEXT);
        INSERT INTO base_location VALUES(1,'iTunes_Control/Music');
        CREATE TABLE container(container_pid INTEGER,name TEXT,sync_id INTEGER,is_src_remote INTEGER,
          cloud_global_id TEXT,smart_criteria BLOB,distinguished_kind INTEGER DEFAULT 0,
          container_type INTEGER DEFAULT 0,is_hidden INTEGER DEFAULT 0,parent_pid INTEGER DEFAULT 0,
          smart_is_folder INTEGER DEFAULT 0,smart_is_dynamic INTEGER DEFAULT 0,
          smart_is_filtered INTEGER DEFAULT 0,smart_is_genius INTEGER DEFAULT 0,
          is_external_vendor_playlist INTEGER DEFAULT 0,is_collaborative INTEGER DEFAULT 0,
          cloud_is_subscribed INTEGER DEFAULT 0,store_cloud_id INTEGER DEFAULT 0);
        INSERT INTO container(container_pid,name,sync_id,is_src_remote,cloud_global_id) VALUES
          (101,'Music',101,1,''),(701,'My playlist',701,1,''),(702,'Smart',702,1,''),(703,'Cloud',703,1,'cloud');
        UPDATE container SET smart_is_dynamic=1 WHERE container_pid=702;
        ''')
        for ident, title, location, size, kind in [(11,'Песня "один"','F00/a.m4a',4,8),
                (12,'Second','F00/b.mp3',3,8),(13,'Cloud','',0,8),(14,'Pending','',0,8),(15,'Video','F00/v.mp4',10,2)]:
            db.execute('INSERT INTO item VALUES(?,?,?,?,?,?,?,?,?)',(ident,kind,41,21,31,1,1,ident-10,1))
            db.execute('INSERT INTO item_extra VALUES(?,?,?,?,?,?,?)',(ident,title,location,size,1000,2021,99))
        if backend == 'iphone':
            db.executescript('''
            PRAGMA user_version=2400060;
            CREATE TABLE item_store(item_pid INTEGER,sync_id INTEGER,artwork_url TEXT);
            INSERT INTO item_store VALUES(11,-2,''),(12,212,''),(13,0,''),(14,214,''),(15,215,'');
            CREATE TABLE _MLDatabaseProperties(key TEXT,value TEXT);
            INSERT INTO _MLDatabaseProperties VALUES('MLSyncClientSyncAnchor','9');
            CREATE TABLE container_item(container_item_pid INTEGER,container_pid INTEGER,item_pid INTEGER,position INTEGER);
            INSERT INTO container_item VALUES(1,701,12,0),(2,701,11,1);
            ''')
        else:
            db.execute('PRAGMA user_version=1140117')
            # Model the unsupported Apple virtual table without depending on its
            # proprietary module. Only this synthetic fixture uses writable_schema.
            db.execute('PRAGMA writable_schema=ON')
            db.execute("INSERT INTO sqlite_master(type,name,tbl_name,rootpage,sql) VALUES('table','item_to_container','item_to_container',0,'CREATE VIRTUAL TABLE item_to_container USING ML3ContainerItemToContainer')")
            db.execute('PRAGMA writable_schema=OFF')


class LibraryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.config = {'backend': 'modern', 'udid': 'fixture-phone'}
        self.device_id = device_identity(self.config)[1]
        self.db = self.root / 'catalogue.db'
        fixture(self.db)
        self.snapshot = read_snapshot(self.db, 'iphone', self.device_id)

    def tearDown(self):
        self.tmp.cleanup()

    def test_reader_is_read_only_and_filters_non_music(self):
        before = self.db.read_bytes()
        s = read_snapshot(self.db, 'iphone', self.device_id)
        self.assertEqual(before, self.db.read_bytes())
        self.assertEqual([t['id'] for t in s['tracks']], ['11','12','13','14'])
        self.assertEqual(summary(s)['counts']['local'], 2)
        self.assertEqual(s['tracks'][2]['availability'], 'cloud')
        self.assertEqual(s['tracks'][3]['availability'], 'pending')

    def test_legacy_virtual_membership_is_unknown_not_empty(self):
        path = self.root / 'legacy.db'
        fixture(path, 'touch')
        s = read_snapshot(path, 'touch', 'touch-fixture')
        self.assertFalse(s['capabilities']['playlist_members']['enabled'])
        self.assertIsNone(s['playlists'][0]['track_count'])
        with self.assertRaisesRegex(LibraryError, 'device-side membership'):
            query(s, {'playlist_id': '701'})

    def test_signed_identifiers_remain_exact_strings_and_wire_numbers(self):
        self.assertEqual(self.snapshot['tracks'][0]['wire_id'], str(2**64-2))
        self.assertEqual(wire_id(str(2**64-2)), -2)
        for value in (True,1.5,'--1','1.0',str(2**64),'١'):
            with self.subTest(value=value), self.assertRaises(LibraryError):pid(value)

    def test_query_search_sort_page_and_private_fields(self):
        result = query(self.snapshot, {'search': 'ПЕСНЯ', 'limit': 1})
        self.assertEqual(result['total'],1)
        self.assertEqual(result['items'][0]['id'],'11')
        self.assertNotIn('asset_path',result['items'][0])
        self.assertNotIn('wire_id',result['items'][0])
        self.assertEqual(query(self.snapshot,{'search': "' OR 1=1"})['total'],0)
        self.assertEqual(query(self.snapshot,{'kind':'albums'})['items'][0]['track_count'],4)
        for p in ({'limit':501},{'offset':-1},{'sort':'DROP TABLE item'},{'limit':True}):
            with self.subTest(p=p), self.assertRaises(LibraryError):query(self.snapshot,p)

    def test_playlist_order_is_preserved(self):
        self.assertEqual([r['id'] for r in query(self.snapshot,{'playlist_id':'701'})['items']],['12','11'])

    def test_snapshot_revision_ignores_capture_time_but_detects_content_changes(self):
        reread = read_snapshot(self.db,'iphone',self.device_id)
        self.assertEqual(self.snapshot['revision'],reread['revision'])
        reread['tracks'][0]['title']='Changed';seal(reread)
        with self.assertRaisesRegex(LibraryError,'changed'):assert_fresh(self.snapshot,reread)

    def test_paths_cannot_escape_music_folder(self):
        for base,location in [('/etc','passwd'),('iTunes_Control/Music','../secret'),('', 'file:///x'),('', 'F00\\x.mp3')]:
            self.assertIsNone(asset_path('iphone',base,location))
        self.assertEqual(asset_path('touch','iTunes_Control/Music','F00/a.m4a'),'/var/mobile/Media/iTunes_Control/Music/F00/a.m4a')
        self.assertEqual(asset_path('touch','Purchases','123.m4a'),'/var/mobile/Media/Purchases/123.m4a')
        self.assertIsNone(asset_path('iphone','Purchases','123.m4a'))

    def test_delete_plan_uses_sync_ids_and_preserves_database_identity(self):
        p=plan(self.snapshot,'delete_tracks',{'track_ids':['11']})
        body=plistlib.loads(base64.b64decode(p['native_payload']))
        self.assertEqual(body['revision'],10)
        self.assertEqual(body['operations'],[
            {'operation':'update_db_info','pid':-40,'db_info':{'primary_container_pid':101}},
            {'operation':'delete_track','pid':-2}])
        self.assertTrue(p['executable'])

    def test_album_selection_expands_then_refuses_cloud_or_pending_tracks(self):
        album=self.snapshot['tracks'][0]['album_id']
        with self.assertRaisesRegex(LibraryError,'Cloud, pending'):
            plan(self.snapshot,'delete_tracks',{'album_ids':[album]})

    def test_metadata_field_validation(self):
        p=plan(self.snapshot,'update_metadata',{'track_ids':['11'],'changes':{'genre':'Metal','year':2020}})
        op=plistlib.loads(base64.b64decode(p['native_payload']))['operations'][1]
        self.assertEqual(op['item'],{'genre':'Metal','year':2020})
        for change in ({'artist':'Other'},{'title':''},{'year':True},{'track_number':-1}):
            with self.subTest(change=change), self.assertRaises(LibraryError):
                plan(self.snapshot,'update_metadata',{'track_ids':['11'],'changes':change})

    def test_out_of_scope_playlist_writes_are_not_exposed(self):
        for action in ('create_playlist','delete_playlist','update_playlist'):
            with self.subTest(action=action), self.assertRaises(LibraryError):
                plan(self.snapshot,action,{'name':'Playlist','playlist_ids':['701']})

    def test_cloud_and_duplicate_selections_refuse_before_building_operations(self):
        for ids in (['13'],['14'],['11','11'],['999']):
            with self.subTest(ids=ids), self.assertRaises(LibraryError):plan(self.snapshot,'delete_tracks',{'track_ids':ids})

    def test_cache_does_not_accept_paths_and_plans_keep_payload_private(self):
        cache=LibraryCache(self.root/'cache');cache.save('snapshots',self.snapshot)
        service=LibraryService(cache)
        response=service.execute('library.plan',self.config,{'snapshot_id':self.snapshot['snapshot_id'],
                                  'action':'delete_tracks','track_ids':['11']})
        self.assertNotIn('native_payload',response)
        self.assertIn('native_payload',cache.load('plans',response['plan_id']))
        with self.assertRaises(LibraryError):cache.load('snapshots','../catalogue.db')

    def test_apply_requires_valid_cached_plan_before_transport(self):
        with patch('library_mutations.apply_device',side_effect=AssertionError('transport called')):
            with self.assertRaises(LibraryError) as error:
                LibraryService().execute('library.apply',{}, {})
        self.assertEqual(error.exception.code,'invalid_cache_id')

    def test_real_child_worker_reads_cache_without_a_device(self):
        local=self.root/'local';data=local/'iMetal'
        LibraryCache(data/'library-cache').save('snapshots',self.snapshot)
        with patch.dict(os.environ,{'LOCALAPPDATA':str(local)}),patch('backend_client.DATA',data):
            result=run_backend('library.query',self.config,payload={'snapshot_id':self.snapshot['snapshot_id'],'search':'Second'})
        self.assertEqual(result['items'][0]['title'],'Second')
        self.assertEqual(list((data/'backend-jobs').iterdir()),[])

    def test_wrong_device_cannot_use_another_snapshot(self):
        cache=LibraryCache(self.root/'cache');cache.save('snapshots',self.snapshot)
        with self.assertRaises(LibraryError) as error:
            LibraryService(cache).execute('library.query',{'host_key':'other-device'}, {'snapshot_id':self.snapshot['snapshot_id']})
        self.assertEqual(error.exception.code,'wrong_device')

    def test_export_never_overwrites_existing_files(self):
        track=self.snapshot['tracks'][0]
        dest=self.root/'export';dest.mkdir()
        existing=dest/filename(track);existing.write_bytes(b'keep')
        with ExportFile(dest,track) as out:
            out.write(b'abcd');receipt=out.finish()
        self.assertEqual(existing.read_bytes(),b'keep')
        self.assertEqual(Path(receipt['path']).read_bytes(),b'abcd')
        self.assertEqual(receipt['sha256'],hashlib.sha256(b'abcd').hexdigest())

    def test_failed_export_removes_only_its_temporary_file(self):
        dest=self.root/'export'
        with self.assertRaises(LibraryError):
            with ExportFile(dest,self.snapshot['tracks'][0]) as out:
                out.write(b'short');out.finish()
        self.assertEqual(list(dest.iterdir()),[])

    def test_export_reserved_names_are_safe(self):
        t=copy.deepcopy(self.snapshot['tracks'][0]);t['title']='CON';t['track_number']=0
        self.assertEqual(filename(t),'_CON.m4a')

    def fake_device(self, stop=None, changed=False):
        data=io.BytesIO(b'abcd')
        before={'st_ifmt':'S_IFREG','st_size':4,'st_mtime':1}
        dev=AsyncMock();dev.info={'ProductType':'iPhone17,1'};dev.__aenter__.return_value=dev
        afc=dev.afc
        afc.stat=AsyncMock(side_effect=[before,{**before,'st_mtime':2}] if changed else [before,before])
        afc.fopen=AsyncMock(return_value=1);afc.fclose=AsyncMock()
        async def read(handle,size):
            if stop:stop.set()
            return data.read(size)
        afc.fread=read
        return dev

    def test_modern_export_streams_audio_and_emits_receipt_without_mutations(self):
        dev=self.fake_device();events=[]
        with patch('library_devices.read_device',AsyncMock(return_value=self.snapshot)),patch('library_devices._modern',AsyncMock(return_value=dev)):
            r=asyncio.run(export_device(self.config,self.snapshot,['11'],str(self.root/'out'),events.append))
        self.assertEqual(r['exported'],1)
        self.assertEqual(events[-1]['file']['sha256'],hashlib.sha256(b'abcd').hexdigest())
        dev.ready.assert_not_called();dev.push_alac.assert_not_called()

    def test_cancelled_or_changed_remote_export_does_not_publish_a_file(self):
        for changed in (False,True):
            stop=threading.Event() if not changed else None
            dev=self.fake_device(stop,changed);dest=self.root/('changed' if changed else 'cancelled')
            with patch('library_devices.read_device',AsyncMock(return_value=self.snapshot)),patch('library_devices._modern',AsyncMock(return_value=dev)):
                with self.assertRaises(LibraryError):asyncio.run(export_device(self.config,self.snapshot,['11'],str(dest),stop=stop))
            self.assertEqual(list(dest.iterdir()),[])

    def test_touch_snapshot_reads_twice_without_device_writes(self):
        content=self.db.read_bytes()
        sftp=Mock()
        def attributes(path):
            if path.endswith(('-wal','-shm')):raise FileNotFoundError(path)
            return SimpleNamespace(st_size=len(content),st_mtime=1,st_mode=0o100644)
        sftp.lstat.side_effect=attributes;sftp.open.side_effect=lambda *a:io.BytesIO(content)
        with patch('library_devices.DATA',self.root):path=_touch_capture(SimpleNamespace(sftp=sftp),'touch-fixture',None)
        self.assertEqual(path.read_bytes(),content)
        self.assertEqual(sftp.open.call_count,2)
        sftp.remove.assert_not_called();sftp.rename.assert_not_called();sftp.chmod.assert_not_called()


if __name__ == '__main__':
    unittest.main()
