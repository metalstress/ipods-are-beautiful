"""Offscreen interaction checks for the library flow; no desktop automation."""
from copy import deepcopy
import os
os.environ['QT_QPA_PLATFORM']='offscreen'
from threading import Event
import unittest
from unittest.mock import Mock, patch

from PySide6.QtCore import Qt, QPoint, QPointF
from PySide6.QtGui import QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLineEdit

from application import Window
from library_canvas import album_cover_key
from library_preview import load_preview
from library_widgets import ASSETS, BarButton, PlaylistWheel
from theme import apply_light_theme
from ui import load_fonts
from version import VERSION


class LibraryInterfaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([]);load_fonts();apply_light_theme(cls.app)

    def setUp(self):
        self.window=Window(preview=True);self.window.preview_devices();self.window.show()
        load_preview(self.window,'songs');self.pane=self.window.library;self.app.processEvents()
        self.pane.change=Mock()

    def tearDown(self):
        self.pane.close_overlay();self.window.close();self.window.deleteLater();self.app.processEvents()

    def captions(self):return [b.text() for b in self.pane.toolbar.right]

    def test_shutdown_does_not_start_queued_cover_reads(self):
        self.pane.queue.append(('cover', {'track_id': '1'}, self.pane.generation))
        self.window.quitting = True
        with patch('library_ui.LibraryJob') as job:
            self.pane.pump()
            job.assert_not_called()

    def test_online_album_cover_is_displayed_and_released_on_close(self):
        album = self.pane.data['albums'][0]
        self.pane.canvas.cover_sources.pop(album['artwork_track_id'])
        key = album_cover_key(album['id'])
        image = (ASSETS / 'preview-cover.png').read_bytes()
        with patch.object(self.window, 'preview', False), \
                patch('online_artwork.find_cover', return_value=image), \
                patch('library_ui.run_backend') as backend:
            self.pane.request_covers([key])
            job = next(iter(self.pane.online_jobs))
            self.assertTrue(job.wait(5000))
            self.app.processEvents()
            backend.assert_not_called()
        self.assertFalse(self.pane.canvas.load_cover(key).isNull())
        self.pane.close_library()
        self.assertNotIn(key, self.pane.canvas.cover_sources)

    def test_existing_device_cover_is_displayed_without_queueing_a_job(self):
        album = self.pane.data['albums'][0]
        self.pane.canvas.cover_sources.pop(album['artwork_track_id'])
        self.pane.data.update(device_id='touch-test', backend='touch')
        key = album_cover_key(album['id'])
        with patch.object(self.window, 'preview', False), \
                patch('library_covers.artwork_path', return_value=ASSETS / 'preview-cover.png'):
            self.pane.request_covers([key])
        self.assertFalse(self.pane.canvas.load_cover(key).isNull())
        self.assertFalse(self.pane.queue or self.pane.online_jobs or self.pane.active)

    def test_library_change_keeps_other_covers_and_updates_only_affected_albums(self):
        load_preview(self.window, 'albums')
        first, second, third = self.pane.data['albums'][:3]
        kept, replaced, removed = [album_cover_key(album['id']) for album in (first, second, third)]
        original = str(ASSETS / 'preview-cover.png')
        for key in (kept, replaced, removed):
            self.pane.show_cover(key, original)
            self.pane.canvas.load_cover(key)
        pixmap = self.pane.canvas.covers[kept]
        self.pane.cover_requests.update((kept, replaced, removed))
        self.pane.expanded = third['id']
        result = deepcopy(self.pane.data)
        result['tracks'] = [t for t in result['tracks'] if t['album_id'] != third['id']]
        result['albums'].remove(third)
        new_cover = (ASSETS / 'preview-cover.png').read_bytes()
        result['changed_covers'] = {second['artwork_track_id']: new_cover}

        job = Mock(task='change')
        self.pane.active = job
        self.pane.finished(job, self.pane.generation, result)

        self.assertIs(self.pane.canvas.covers[kept], pixmap)
        self.assertEqual(self.pane.canvas.cover_sources[kept], original)
        self.assertIs(self.pane.canvas.cover_sources[replaced], new_cover)
        self.assertNotIn(replaced, self.pane.canvas.covers)
        self.assertNotIn(removed, self.pane.canvas.cover_sources)
        self.assertEqual(self.pane.cover_requests, {kept})
        self.assertIsNone(self.pane.expanded)

    def test_background_artwork_update_keeps_selection_and_rebinds_a_queued_edit(self):
        load_preview(self.window,'albums')
        self.pane.data['device_id']='iphone-test'
        album,other=self.pane.data['albums'][:2]
        key=album_cover_key(other['id'])
        self.pane.show_cover(key,str(ASSETS/'preview-cover.png'))
        original=self.pane.canvas.load_cover(key)
        self.pane.selecting=True;self.pane.selected={album['id']};self.pane.expanded=album['id']
        old=self.pane.data['snapshot_id']
        self.pane.queue.append(('change',{'snapshot_id':old,'operations':[]},self.pane.generation))
        result=deepcopy(self.pane.data);result['snapshot_id']='updated-snapshot'
        data=(ASSETS/'preview-cover.png').read_bytes()
        self.pane.accept_artwork_repair({'library':result,'changed_covers':{album['artwork_track_id']:data}})
        self.assertEqual(self.pane.selected,{album['id']})
        self.assertEqual(self.pane.expanded,album['id'])
        self.assertIs(self.pane.canvas.covers[key],original)
        self.assertEqual(self.pane.queue[0][1]['snapshot_id'],'updated-snapshot')

    def test_album_reveal_moves_rows_and_finishes_on_the_selected_album(self):
        load_preview(self.window, 'albums')
        canvas = self.pane.canvas
        first, second = self.pane.data['albums'][:2]
        self.pane.item_action({'action': 'album', 'id': first['id']})
        canvas.reveal_animation.setCurrentTime(40)
        partial = [b for b in canvas.blocks if b.get('inline')]
        self.assertTrue(0 < len(partial) < 9)
        self.assertIn('visible_bottom', partial[-1])

        self.pane.item_action({'action': 'album', 'id': second['id']})
        canvas.reveal_animation.setCurrentTime(canvas.reveal_animation.duration())
        self.assertEqual(canvas.album_reveals, {second['id']: 1.0})
        self.assertEqual(len([b for b in canvas.blocks if b.get('inline')]), 9)

        self.pane.item_action({'action': 'album', 'id': second['id']})
        canvas.reveal_animation.setCurrentTime(canvas.reveal_animation.duration())
        self.assertFalse(any(b.get('inline') for b in canvas.blocks))

    def test_tab_switch_transitions_to_a_live_library_view(self):
        self.pane.set_tab('playlists')
        self.assertEqual(self.pane.view, 'playlists')
        self.assertTrue(self.pane.tab_transition.isVisible())
        self.assertTrue(self.pane.tab_transition.testAttribute(Qt.WA_TransparentForMouseEvents))
        self.pane.set_tab('albums')
        animation = self.pane.tab_transition.animation
        animation.setCurrentTime(animation.duration())
        self.assertEqual(self.pane.canvas.view, 'albums')
        self.assertFalse(self.pane.tab_transition.isVisible())

    def test_device_cover_can_start_while_other_albums_are_searching_online(self):
        self.pane.online_jobs[Mock()] = 'archive'
        record = ('cover', {'track_id': '1'}, self.pane.generation)
        self.pane.queue.append(record)
        with patch.object(self.pane, 'start_job') as start:
            self.pane.pump()
        start.assert_called_once_with(*record)

    def test_slow_archive_does_not_block_deezer_and_device_is_last(self):
        first, second = self.pane.data['albums'][:2]
        for album in (first, second):
            self.pane.canvas.cover_sources.pop(album['artwork_track_id'])
        first_key, second_key = [album_cover_key(album['id']) for album in (first, second)]
        image_path = ASSETS / 'preview-cover.png'
        calls = []
        archive_ready = Event()
        release_archive = Event()

        def online(album, stop, provider):
            calls.append((provider, album['id']))
            if provider == 'archive':
                archive_ready.set()
                release_archive.wait(5)
            return image_path.read_bytes() if album is second else None

        def device(command, config, **kwargs):
            calls.append(('device', kwargs['payload']['track_id']))
            return {'path': str(image_path)}

        with patch.object(self.window, 'preview', False), \
                patch('online_artwork.find_cover', side_effect=online), \
                patch('library_ui.run_backend', side_effect=device):
            try:
                self.pane.request_covers([first_key])
                deezer = next(iter(self.pane.online_jobs))
                self.assertTrue(deezer.wait(5000))
                self.app.processEvents()
                archive = next(iter(self.pane.online_jobs))
                self.assertTrue(archive_ready.wait(5))
                self.pane.request_covers([second_key])
                deezer = next(job for job, provider in self.pane.online_jobs.items() if provider == 'deezer')
                self.assertTrue(deezer.wait(5000))
                self.app.processEvents()
                self.assertFalse(self.pane.canvas.load_cover(second_key).isNull())
                self.assertIsNone(self.pane.active)
            finally:
                release_archive.set()
            self.assertTrue(archive.wait(5000))
            self.app.processEvents()
            self.assertTrue(self.pane.active.wait(5000))
            self.app.processEvents()
        self.assertEqual(calls, [
            ('deezer', first['id']), ('archive', first['id']),
            ('deezer', second['id']), ('device', first['artwork_track_id']),
        ])
        self.assertFalse(self.pane.canvas.load_cover(first_key).isNull())

    def test_scrolling_requests_visible_and_nearby_album_rows(self):
        self.pane.data['albums'] = [dict(self.pane.data['albums'][0], id=str(i)) for i in range(100)]
        self.pane.set_tab('albums')
        self.app.processEvents()
        rows = self.pane.canvas.blocks
        scroll = self.pane.scroll.verticalScrollBar()
        with patch.object(self.pane, 'request_covers') as request:
            scroll.setValue(rows[3]['y'])
            for _ in range(20):
                if request.called:break
                QTest.qWait(50)
        self.assertTrue(request.called,'Scrolling did not schedule album-cover prefetch.')
        keys = request.call_args.args[0]
        self.assertEqual(keys[0], album_cover_key(rows[3]['items'][0][1]['id']))
        first_below_view = next(row for row in rows
                                if row['y'] >= scroll.value() + self.pane.scroll.viewport().height())
        self.assertIn(album_cover_key(first_below_view['items'][0][1]['id']), keys)
        self.assertIn(album_cover_key(rows[1]['items'][0][1]['id']), keys)
        self.assertNotIn(album_cover_key(rows[0]['items'][0][1]['id']), keys)
        self.assertNotIn(album_cover_key(rows[-1]['items'][0][1]['id']), keys)

    def test_song_selection_shows_add_only_after_a_song_is_selected(self):
        self.pane.begin_selection();self.assertNotIn('Add to playlist',self.captions())
        block=next(b for b in self.pane.canvas.blocks if b['type']=='track')
        QTest.mouseClick(self.pane.canvas,Qt.LeftButton,pos=QPoint(100,block['y']+15))
        self.assertIn('Add to playlist',self.captions());self.assertEqual(len(self.pane.selected),1)
        self.pane.cancel_selection();self.assertFalse(self.pane.selected);self.assertEqual(self.captions(),['Select'])

    def test_iphone_views_hide_unavailable_cloud_tracks_without_changing_playlist_members(self):
        load_preview(self.window, 'albums')
        self.pane.data['backend'] = 'iphone'
        for track in self.pane.data['tracks']:
            if track['album_id'] == 'a0':
                track['availability'] = 'cloud'
            elif track['album_id'] == 'a1':
                track['availability'] = 'pending'
        members = list(self.pane.data['playlists'][0]['members'])
        self.pane.refresh()
        self.assertNotIn('a0', [album['id'] for album in self.pane.canvas.rows])
        self.assertNotIn('a1', [album['id'] for album in self.pane.canvas.rows])
        self.pane.set_tab('tracks')
        self.assertTrue(all(track['availability'] == 'local' for track in self.pane.canvas.rows))
        self.pane.set_tab('artists')
        self.assertEqual([artist['id'] for artist in self.pane.canvas.rows], ['king'])
        self.assertEqual(self.pane.data['playlists'][0]['members'], members)
        self.assertEqual(len(self.pane.data['tracks']), 135)

    def test_picker_accepts_click_and_wheel_and_submits_the_chosen_playlist(self):
        self.pane.selected={'1','2'};self.pane.pick_playlist();self.app.processEvents()
        wheel=self.pane.overlay.dialog.findChild(PlaylistWheel)
        QTest.mouseClick(wheel,Qt.LeftButton,pos=QPoint(88,125));self.assertEqual(wheel.index,1)
        event=QWheelEvent(QPointF(88,80),QPointF(88,80),QPoint(),QPoint(0,-120),Qt.NoButton,Qt.NoModifier,Qt.NoScrollPhase,False)
        self.app.sendEvent(wheel,event);self.assertEqual(wheel.index,2)
        save=next(b for b in self.pane.overlay.dialog.findChildren(BarButton) if b.text()=='Save');save.click()
        operations=self.pane.change.call_args.args[0]
        self.assertEqual(operations,[{'action':'add_to_playlist','playlist_id':'1002','track_ids':['1','2']}])

    def test_cancel_picker_never_submits_and_keeps_the_selection(self):
        self.pane.selected={'1'};self.pane.pick_playlist()
        next(b for b in self.pane.overlay.dialog.findChildren(BarButton) if b.text()=='Cancel').click()
        self.pane.change.assert_not_called();self.assertEqual(self.pane.selected,{'1'})

    def test_empty_picker_cannot_save_and_empty_playlists_can_create(self):
        self.pane.data['playlists']=[];self.pane.set_tab('playlists')
        self.assertTrue(any(b.text()=='New' and b.isEnabled() for b in self.pane.toolbar.right))
        self.pane.pick_playlist();save=next(b for b in self.pane.overlay.dialog.findChildren(BarButton) if b.text()=='Save')
        self.assertFalse(save.isEnabled())

    def test_playlist_rename_uses_selected_id_and_delete_is_playlist_only(self):
        self.pane.set_tab('playlists');self.pane.begin_editing();self.pane.item_action({'action':'playlist_actions','id':'1000'})
        self.pane.rename_playlist();field=self.pane.overlay.dialog.findChild(QLineEdit);self.assertEqual(field.text(),'School days')
        field.setText('New name');next(b for b in self.pane.overlay.dialog.findChildren(BarButton) if b.text()=='Save').click()
        self.pane.change.assert_called_with([{'action':'rename_playlist','playlist_id':'1000','name':'New name'}])
        self.pane.delete_playlist();self.pane.change.assert_called_with([{'action':'delete_playlist','playlist_id':'1000'}])

    def test_playlist_members_are_staged_until_done_and_cancel_discards(self):
        self.pane.set_tab('playlists');self.pane.item_action({'action':'playlist','id':'1000'});self.pane.begin_playlist_edit()
        self.pane.selected={'1'};self.pane.remove_draft_songs()
        self.assertEqual(self.pane.draft,['2','3']);self.pane.change.assert_not_called()
        self.pane.save_playlist();self.pane.change.assert_called_with([{'action':'set_playlist_tracks','playlist_id':'1000','track_ids':['2','3']}])
        self.pane.begin_playlist_edit();self.pane.selected={'2'};self.pane.remove_draft_songs();self.pane.cancel_playlist_edit()
        self.assertEqual(self.pane.playlist_record()['members'],['1','2','3'])

    def test_album_selection_expands_to_its_tracks_only(self):
        self.pane.set_tab('albums');self.pane.begin_selection();self.pane.item_action({'action':'album','id':'a0'})
        expected={t['id'] for t in self.pane.data['tracks'] if t['album_id']=='a0'}
        self.assertEqual(self.pane.selected,expected)
        self.pane.item_action({'action':'album','id':'a0'});self.assertFalse(self.pane.selected)

    def test_info_save_changes_only_edited_fields(self):
        self.pane.info(['1'],True);fields=self.pane.overlay.dialog.findChildren(QLineEdit)
        year=next(f for f in fields if f.text()=='2021');year.setFocus();year.selectAll();QTest.keyClicks(year,'2022')
        next(b for b in self.pane.overlay.dialog.findChildren(BarButton) if b.text()=='Save').click()
        self.pane.change.assert_called_with([{'action':'update_metadata','track_ids':['1'],'changes':{'year':2022}}])

    def test_release_version_is_120(self):self.assertEqual(VERSION,'1.2.0')

if __name__=='__main__':unittest.main()
