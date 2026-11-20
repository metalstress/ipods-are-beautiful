import hashlib
import io
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

from library_devices import _touch_capture_hashed,TOUCH_LIBRARY
from library_model import LibraryError


class CaptureTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.payload=b'bounded read-only catalogue fixture'
        self.digest=hashlib.sha256(self.payload).hexdigest()
        self.sftp=Mock()
        def stat(path):
            if path!=TOUCH_LIBRARY:raise FileNotFoundError(path)
            return SimpleNamespace(st_size=len(self.payload),st_mtime=1,st_mode=0o100600)
        self.sftp.lstat.side_effect=stat
        self.sftp.open.side_effect=lambda *args:io.BytesIO(self.payload)
        self.session=SimpleNamespace(sftp=self.sftp,run=Mock(side_effect=lambda command,**kwargs:
            '/usr/bin/sha256sum' if command.startswith('command -v') else self.digest+'  '+TOUCH_LIBRARY))

    def tearDown(self):self.tmp.cleanup()

    def test_unchanged_database_is_not_downloaded_a_second_time(self):
        with patch('library_devices.DATA',self.root):
            a=_touch_capture_hashed(self.session,'fixture',None)
            b=_touch_capture_hashed(self.session,'fixture',None)
        self.assertEqual(a.read_bytes(),self.payload);self.assertEqual(b.read_bytes(),self.payload)
        self.assertEqual(self.sftp.open.call_count,1)
        self.assertTrue(all(call.args[0].startswith(('command -v','sha256sum ')) for call in self.session.run.call_args_list))

    def test_tampered_cache_is_replaced_from_verified_device_bytes(self):
        with patch('library_devices.DATA',self.root):
            _touch_capture_hashed(self.session,'fixture',None)
            (self.root/'library-raw-cache'/'fixture'/self.digest).write_bytes(b'tampered')
            captured=_touch_capture_hashed(self.session,'fixture',None)
        self.assertEqual(captured.read_bytes(),self.payload);self.assertEqual(self.sftp.open.call_count,2)

    def test_changed_checksum_never_returns_a_mixed_snapshot(self):
        state={'count':0}
        def run(command,**kwargs):
            if command.startswith('command -v'):return '/usr/bin/sha256sum'
            state['count']+=1
            return (self.digest if state['count']%2 else '0'*64)+'  '+TOUCH_LIBRARY
        self.session.run.side_effect=run
        with patch('library_devices.DATA',self.root),self.assertRaises(LibraryError):
            _touch_capture_hashed(self.session,'fixture',None)

if __name__=='__main__':unittest.main()
