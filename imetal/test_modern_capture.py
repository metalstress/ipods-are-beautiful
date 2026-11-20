import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from pymobiledevice3.exceptions import AfcFileNotFoundError

from modern_music import DeviceFileChangedError, ModernDevice


class ModernCaptureTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.device = ModernDevice('fixture', self.folder.name)
        self.device.afc = AsyncMock()
        self.captures = 0
        self.stable_after = 0
        self.wal_disappears = False
        self.change_during_read = False
        self.device.afc.stat.side_effect = self.stat
        self.device.read_file = AsyncMock(side_effect=self.read_file)
        self.catalogue = {'anchor': 9}

    async def stat(self, remote):
        name = remote.rsplit('/', 1)[-1]
        if name == 'MediaLibrary.sqlitedb-wal' and self.wal_disappears and self.captures > 0:
            raise AfcFileNotFoundError('checkpoint removed WAL', None, remote)
        if name == 'MediaLibrary.sqlitedb-shm':
            self.fail('The transient SHM file must not participate in capture')
        return {'st_ifmt': 'S_IFREG', 'st_size': 4,
                'st_mtime': min(self.captures, self.stable_after)}

    async def read_file(self, remote):
        if self.change_during_read:
            self.change_during_read = False
            raise DeviceFileChangedError('Device file changed during read')
        if remote.endswith('iTunesPrefs.plist'):
            self.captures += 1
        return b'data'

    async def capture(self):
        with patch('modern_music.library_identity', return_value='fixture-identity'), \
                patch('modern_music.read_catalogue', return_value=self.catalogue), \
                patch('modern_music.asyncio.sleep', new_callable=AsyncMock):
            return await self.device.snapshot('test')

    async def test_waits_through_several_library_commits(self):
        self.stable_after = 4
        self.assertIs(await self.capture(), self.catalogue)
        self.assertEqual(self.captures, 5)

    async def test_checkpoint_does_not_leave_a_stale_wal_in_the_next_attempt(self):
        self.wal_disappears = True
        self.assertIs(await self.capture(), self.catalogue)
        self.assertEqual(self.captures, 2)
        self.assertFalse((self.device.backup / 'MediaLibrary.sqlitedb-wal').exists())

    async def test_file_changing_during_read_is_retried(self):
        self.change_during_read = True
        self.assertIs(await self.capture(), self.catalogue)

    async def test_never_publishes_a_catalogue_that_keeps_changing(self):
        self.stable_after = 100
        self.device.catalogue = previous = {'anchor': 8}
        with self.assertRaisesRegex(RuntimeError, 'Music is updating'):
            await self.capture()
        self.assertIs(self.device.catalogue, previous)
        self.assertFalse(hasattr(self.device, 'backup'))
        self.assertEqual(self.captures, 8)
