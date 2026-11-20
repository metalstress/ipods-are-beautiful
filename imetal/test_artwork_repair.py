from contextlib import nullcontext
import asyncio
from copy import deepcopy
import io
from pathlib import Path
import tempfile
from threading import Event
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import AsyncMock, Mock, patch

from PIL import Image
from artwork_repair import find_artwork, missing_albums, repair_one_album
from library_artwork import prepare_cover
from library_model import LibraryError


def track(ident, covered=False, availability='local', editable=True):
    return dict(id=str(ident), title='Song '+str(ident), artist='The Prodigy',
                album='The Day Is My Enemy', album_id='album:1', album_artist='The Prodigy',
                availability=availability, can_plan_delete=editable, has_artwork=covered,
                origin='synced', artwork_id='100' if covered else '0',
                year=2015, genre='Electronic', duration_ms=1000, size_bytes=100)


class RepairService:
    def __init__(self, root, initial, fresh=None):
        self.root=root
        self.snapshots={'before':deepcopy(initial), 'fresh':deepcopy(fresh or initial)}
        self.plans={}; self.reads=0; self.applied=[]; self.error=None
        self.cache=SimpleNamespace(load=lambda kind,ident: self.snapshots[ident] if kind=='snapshots' else self.plans[ident])

    def execute(self, command, config, payload, **kwargs):
        if command=='library.read':
            key='before' if self.reads==0 else 'fresh'; self.reads+=1
            self.snapshots[key]['snapshot_id']=key
            return {'snapshot_id':key}
        if command=='library.plan':
            self.plans['plan']={'cover':prepare_cover(payload['cover_path'], self.root/'library-artwork')}
            self.target_ids=payload['track_ids']
            return {'plan_id':'plan'}
        if command=='library.apply':
            self.applied.append(payload)
            if self.error:raise self.error
            self.snapshots['after']=deepcopy(self.snapshots['fresh'])
            for item in self.snapshots['after']['tracks']:
                if item['id'] in self.target_ids:item['has_artwork']=True
            return {'snapshot_id':'after'}
        if command=='library.browse':return self.snapshots[payload['snapshot_id']]
        raise AssertionError(command)


class ArtworkRepairTests(TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.patch=patch('artwork_repair.DATA',self.root);self.patch.start()
        self.config={'backend':'modern'}
        self.image=io.BytesIO();Image.new('RGB',(64,64),'red').save(self.image,'JPEG')
        self.data=self.image.getvalue()
        self.initial={'tracks':[track(1),track(2),track(3,True),track(4,availability='cloud',editable=False),track(5,editable=False)]}
        self.service=RepairService(self.root,self.initial)

    def tearDown(self):
        self.patch.stop();self.temp.cleanup()

    def run_repair(self, **kwargs):
        with patch('artwork_repair.find_artwork',return_value=self.data):
            return repair_one_album(self.config,service=self.service,**kwargs)

    def test_only_missing_editable_local_tracks_receive_artwork(self):
        result=self.run_repair()
        self.assertEqual((result['repaired'],self.service.target_ids),(2,['1','2']))
        self.assertEqual(len(self.service.applied),1)
        self.assertEqual(self.service.snapshots['after']['tracks'][2:],self.initial['tracks'][2:])
        self.assertEqual(list(self.root.glob('artwork-repair-*')),[])
        self.assertEqual(list((self.root/'library-artwork').iterdir()),[])

    def test_user_cover_added_during_lookup_is_preserved(self):
        fresh=deepcopy(self.initial)
        for t in fresh['tracks']:t['has_artwork']=True
        self.service=RepairService(self.root,self.initial,fresh)
        result=self.run_repair()
        self.assertEqual(result['repaired'],0)
        self.assertFalse(self.service.applied)

    def test_no_match_leaves_audio_untouched(self):
        with patch('artwork_repair.find_artwork',return_value=None):
            result=repair_one_album(self.config,service=self.service)
        self.assertEqual(result['checked_album_ids'],['album:1'])
        self.assertEqual(result['repaired'],0)
        self.assertFalse(self.service.applied)

    def test_cancelled_lookup_does_not_submit_a_plan(self):
        stop=Event()
        def found(*args):stop.set();return self.data
        with patch('artwork_repair.find_artwork',side_effect=found):
            result=repair_one_album(self.config,stop=stop,service=self.service)
        self.assertTrue(result['cancelled'])
        self.assertFalse(self.service.applied)
        self.assertEqual(result['checked_album_ids'],[])

    def test_busy_native_sync_is_deferred_without_blind_retry(self):
        self.service.error=LibraryError('change_not_verified','busy',cause='device_busy')
        result=self.run_repair()
        self.assertTrue(result['deferred'])
        self.assertEqual(len(self.service.applied),1)
        self.assertEqual(result['checked_album_ids'],[])
        self.assertEqual(list((self.root/'library-artwork').iterdir()),[])

    def test_unverified_commit_retains_its_owned_recovery_input(self):
        self.service.error=LibraryError('change_not_verified','Lost response')
        result=self.run_repair()
        self.assertEqual(result['code'],'change_not_verified')
        self.assertEqual(len(self.service.applied),1)
        self.assertEqual(len(list((self.root/'library-artwork').iterdir())),1)

    def test_excluded_and_fully_covered_albums_are_not_looked_up(self):
        self.assertFalse(missing_albums(self.initial,['album:1']))
        self.assertFalse(missing_albums({'tracks':[track(1,True)]}))

    def test_large_album_uses_bounded_sessions_and_keeps_remaining_tracks_pending(self):
        self.service=RepairService(self.root,{'tracks':[track(i) for i in range(501)]})
        result=self.run_repair()
        self.assertEqual(result['repaired'],499)
        self.assertTrue(result['remaining'])
        self.assertEqual(result['checked_album_ids'],[])

    def test_partial_album_reuses_device_cover_before_the_internet(self):
        source=self.root/'existing.jpg';source.write_bytes(self.data)
        tracks=[track(1,True),track(2)]
        with patch('device_lock.device_lock',return_value=nullcontext()), \
                patch('library_covers.read_cover',new_callable=AsyncMock,return_value={'path':str(source)}), \
                patch('online_artwork.find_cover') as lookup:
            data=find_artwork(self.config,self.initial,{},tracks,Event())
        self.assertEqual(data,self.data)
        lookup.assert_not_called()

    def test_stalled_preflight_read_is_cancelled_before_upload_takes_over(self):
        from library_backend import capture_cancellable
        async def scenario():
            stop=Event(); started=asyncio.Event(); closed=asyncio.Event()
            async def stalled(*args):
                started.set()
                try:await asyncio.Event().wait()
                finally:closed.set()
            with patch('library_backend.read_device',side_effect=stalled):
                task=asyncio.create_task(capture_cancellable(self.config,stop))
                await started.wait(); stop.set()
                with self.assertRaises(LibraryError) as error:
                    await asyncio.wait_for(task,1)
                self.assertEqual(error.exception.code,'cancelled')
                self.assertTrue(closed.is_set())
        asyncio.run(scenario())
