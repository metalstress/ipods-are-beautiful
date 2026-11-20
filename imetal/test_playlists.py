"""Playlist protocol, safety and ordering regressions; no device required."""
import base64
import copy
from pathlib import Path
import plistlib
import tempfile
import unittest
from unittest.mock import Mock, patch

from library_catalogue import read_snapshot
from library_model import LibraryError, pid
from library_plans import plan
from library_playlists import verify_playlist
from library_touch_playlists import apply
from test_library import fixture


class PlaylistTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        db=self.root/'db';fixture(db);self.snapshot=read_snapshot(db,'iphone','fixture')

    def tearDown(self):self.temp.cleanup()

    def operation(self,proposal):return plistlib.loads(base64.b64decode(proposal['native_payload']))['operations'][1]

    def test_add_preserves_order_and_does_not_duplicate_existing_members(self):
        proposal=plan(self.snapshot,'add_to_playlist',{'playlist_id':'701','track_ids':['11','12']})
        self.assertEqual(proposal['member_ids'],['12','11']);self.assertTrue(proposal['noop'])
        self.assertEqual(self.operation(proposal)['item_to_container'],[212,-2])

    def test_delete_playlist_never_contains_track_deletions(self):
        proposal=plan(self.snapshot,'delete_playlist',{'playlist_id':'701'})
        self.assertEqual(self.operation(proposal),{'operation':'delete_playlist','pid':701})
        self.assertEqual(proposal['track_ids'],[]);self.assertEqual(proposal['size_bytes'],0)
        after=copy.deepcopy(self.snapshot);after['playlists']=[p for p in after['playlists'] if p['id']!='701']
        verify_playlist(self.snapshot,after,proposal)
        after['tracks'].pop()
        with self.assertRaises(LibraryError):verify_playlist(self.snapshot,after,proposal)

    def test_empty_and_reordered_membership_use_complete_native_list(self):
        clear=plan(self.snapshot,'set_playlist_tracks',{'playlist_id':'701','track_ids':[]})
        self.assertEqual(self.operation(clear)['item_to_container'],[])
        reorder=plan(self.snapshot,'set_playlist_tracks',{'playlist_id':'701','track_ids':['11','12']})
        self.assertEqual(self.operation(reorder)['item_to_container'],[-2,212])

    def test_smart_cloud_hidden_and_primary_playlists_are_read_only(self):
        for ident in ['101','702','703','999']:
            with self.subTest(ident=ident),self.assertRaises(LibraryError):
                plan(self.snapshot,'delete_playlist',{'playlist_id':ident})

    def test_names_and_selections_are_validated_before_submission(self):
        for value in ['', '   ', 'a\x00b', 'a'*256, None]:
            with self.subTest(value=value),self.assertRaises(LibraryError):plan(self.snapshot,'create_playlist',{'name':value})
        for ids in [['11','11'],['13'],['999']]:
            with self.subTest(ids=ids),self.assertRaises(LibraryError):plan(self.snapshot,'add_to_playlist',{'playlist_id':'701','track_ids':ids})

    def test_unrelated_playlist_or_member_change_is_rejected(self):
        proposal=plan(self.snapshot,'rename_playlist',{'playlist_id':'701','name':'New'})
        after=copy.deepcopy(self.snapshot);after['playlists'][1]['name']='New'
        verify_playlist(self.snapshot,after,proposal)
        after['playlists'][2]['name']='Other'
        with self.assertRaises(LibraryError):verify_playlist(self.snapshot,after,proposal)

    def test_touch_lookup_accepts_both_signed_representations(self):
        proposal=plan(self.snapshot,'rename_playlist',{'playlist_id':'701','name':'New'})
        proposal['playlist_id']=str(2**64-2)
        bridge=Mock();bridge.execute.return_value={}
        with patch('library_touch_playlists.bridge_for',return_value=bridge):apply(Mock(),self.snapshot,proposal)
        code=bridge.execute.call_args.args[0]
        self.assertIn('@"18446744073709551614"',code);self.assertIn('@"-2"',code)
        self.assertNotIn('newWithDictionary',code)

    def test_created_touch_id_is_normalized_without_losing_bits(self):
        proposal=plan(self.snapshot,'create_playlist',{'name':'New'})
        after=copy.deepcopy(self.snapshot)
        after['playlists'].append({'id':pid('-2'),'name':'New','track_ids':[]})
        verify_playlist(self.snapshot,after,proposal,created_id='-2')

    def test_membership_writer_uses_native_integer_buffers_and_notifications(self):
        proposal=plan(self.snapshot,'set_playlist_tracks',{'playlist_id':'701','track_ids':['12','11']})
        bridge=Mock();bridge.execute.return_value={}
        with patch('library_touch_playlists.bridge_for',return_value=bridge):apply(Mock(),self.snapshot,proposal)
        code=bridge.execute.call_args.args[0]
        self.assertIn('getReturnValue:memory',code);self.assertIn('getBytes:argument length:8',code)
        self.assertIn('setTracksWithPersistentIDs:',code);self.assertIn('notify:YES',code)
        self.assertIn('defaultMediaLibrary] libraryDataProvider',code)
        self.assertIn('NSScanner scannerWithString:',code)
        self.assertIn('scanLongLong:memberArgument',code)
        self.assertNotIn('JSONObjectWithData:',code)

    def test_create_allocates_only_an_unoccupied_playlist_identity(self):
        proposal=plan(self.snapshot,'create_playlist',{'name':'New','new_playlist_id':'101'})
        self.assertNotIn(proposal['new_playlist_id'],self.snapshot['native']['occupied_wire_ids'])
        self.assertNotEqual(proposal['new_playlist_id'],'101')
        self.assertEqual(self.operation(proposal)['operation'],'insert_playlist')

if __name__=='__main__':unittest.main()
