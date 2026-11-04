import plistlib
from pathlib import Path
from types import SimpleNamespace
import unittest
import sys
sys.path.insert(0,str(Path(__file__).resolve().parent.parent/'imetal'))
from modern_music import library_identity,insert_operation


class ModernProtocolTests(unittest.TestCase):
    def test_preserves_existing_host_identity_with_exact_integer_precision(self):
        raw=bytearray(1232);raw[:9]=b'frpd\x01\x00\x19\x00\x01'
        a,b=0x123456789ABCDEF0,0x0FEDCBA987654321
        raw[12:20]=a.to_bytes(8,'little');raw[20:28]=b.to_bytes(8,'little')
        self.assertEqual(library_identity(bytes(raw)),f'{a^b:016X}')
        raw[8]=0
        with self.assertRaises(RuntimeError):library_identity(bytes(raw))

    def test_append_only_operation_keeps_database_and_primary_playlist(self):
        catalog={'occupied':{17,18,19},'db_pid':-9155555555555555555,
                 'primary_container_pid':9155555555555555556,'anchor':5,'groups':[], 'artwork_ids':set()}
        track=SimpleNamespace(title='Песня №1',artist='Artist',album='Album',album_artist='Artist',
            duration=3,rate=44100,bits=24,channels=2,number=1,total=2,year=2026,
            genre='',copyright='',cover=b'')
        pid,data,cover=insert_operation(catalog,track);body=plistlib.loads(data)
        self.assertEqual(body['revision'],6)
        self.assertEqual([o['operation'] for o in body['operations']],['update_db_info','insert_track'])
        db,insert=body['operations']
        self.assertEqual(db['pid'],catalog['db_pid'])
        self.assertEqual(db['db_info']['primary_container_pid'],catalog['primary_container_pid'])
        self.assertNotIn(pid,catalog['occupied'])
        self.assertEqual(insert['item']['title'],track.title)
        self.assertEqual(insert['avformat_info']['duration'],132300)
        self.assertNotIn('location',insert['location'])

    def test_existing_album_group_is_reused(self):
        catalog={'occupied':{7,8,9},'db_pid':7,'primary_container_pid':8,'anchor':0,
                 'groups':[dict(album='Album',album_artist='Artist',sync_id=19,artist_sync_id=20)],
                 'artwork_ids':{100,105}}
        track=SimpleNamespace(title='Song',artist='Artist',album='Album',album_artist='Artist',
            duration=1,rate=96000,bits=24,channels=2,number=1,total=1,year=0,
            genre='',copyright='',cover=b'\xff\xd8test')
        _,data,_=insert_operation(catalog,track);item=plistlib.loads(data)['operations'][1]['item']
        self.assertEqual((item['album_pid'],item['artist_pid']),(19,20))
        self.assertEqual(item['artwork_cache_id'],106)
        self.assertEqual(item['artwork_checksum'],23)


if __name__=='__main__':unittest.main()
