from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import tempfile
import unittest

from engine import Bridge
from transport import Session


class CleanupTests(unittest.TestCase):
    def setUp(self):
        self.ready = dict(pid='18446744073709551610', title='Song', artist='Artist', album='Album',
                          duration=100., created='815315825.923041', store_id='0', size=123,
                          asset='/var/mobile/Media/Purchases/retained.m4a')
        self.ghost = dict(self.ready, pid='18446744073709551611', asset='', size=0)

    def test_only_new_empty_twin_of_exact_import_is_selected(self):
        others = [dict(self.ghost, pid='2', created='815315826'),
                  dict(self.ghost, pid='3', artist='Other Artist'),
                  dict(self.ghost, pid='4', duration=103),
                  dict(self.ghost, pid='5', store_id='9'),
                  dict(self.ghost, pid='6', asset='/var/mobile/Media/another.m4a', size=123),
                  dict(self.ghost, pid='7', created='')]
        self.assertEqual(Bridge.placeholders([self.ready, self.ghost] + others,
                                             self.ready, set()), [self.ghost])
        self.assertEqual(Bridge.placeholders([self.ghost], self.ready, {self.ghost['pid']}), [])

    def test_never_pass_real_file_to_empty_item_remover(self):
        with self.assertRaises(ValueError):
            Bridge(None).remove_empty_items([self.ready])

    def test_finalization_preserves_ready_asset_and_verifies_removal(self):
        session = SimpleNamespace(sftp=SimpleNamespace(stat=lambda p: SimpleNamespace(st_size=123)))
        b = Bridge(session)
        with tempfile.TemporaryDirectory() as tmp, patch('engine.DATA', Path(tmp)), \
                patch('engine.time.sleep'), patch.object(b, 'entries', side_effect=[
                    [self.ready, self.ghost], [self.ready]]), \
                patch.object(b, 'remove_empty_items') as remove:
            b.finish_imports([(self.ready, set())], 0)
            remove.assert_called_once_with([self.ghost])
            self.assertEqual(len(list((Path(tmp) / 'cleanup').glob('*.json'))), 1)

    def test_missing_retained_asset_aborts_before_removal(self):
        b = Bridge(SimpleNamespace(sftp=SimpleNamespace(stat=lambda p: SimpleNamespace(st_size=0))))
        with patch('engine.time.sleep'), patch.object(b, 'entries', return_value=[self.ready, self.ghost]), \
                patch.object(b, 'remove_empty_items') as remove, self.assertRaises(RuntimeError):
            b.finish_imports([(self.ready, set())], 0)
        remove.assert_not_called()

    def test_removal_must_be_confirmed_by_native_catalogue(self):
        b = Bridge(SimpleNamespace(sftp=SimpleNamespace(stat=lambda p: SimpleNamespace(st_size=123))))
        with tempfile.TemporaryDirectory() as tmp, patch('engine.DATA', Path(tmp)), \
                patch('engine.time.sleep'), patch.object(b, 'entries', return_value=[self.ready, self.ghost]), \
                patch.object(b, 'remove_empty_items'), self.assertRaises(RuntimeError):
            b.finish_imports([(self.ready, set())], 0)


class EarlyExitChannel:
    """Exit status arrives first; stdout follows, then EOF (a fast SSH command)."""
    def __init__(self):
        self.tick = 0
        self.consumed = False
        self.closed = False
    @property
    def eof_received(self): return self.tick >= 2
    def exit_status_ready(self): return True
    def recv_ready(self): return self.tick >= 1 and not self.consumed
    def recv_stderr_ready(self): return False
    def recv(self, size):
        self.consumed = True
        return b'catalogue rows\n'
    def recv_exit_status(self): return 0


class SSHOutputTests(unittest.TestCase):
    def test_wait_for_output_after_early_exit_status(self):
        channel = EarlyExitChannel()
        session = Session({})
        session.client = SimpleNamespace(exec_command=lambda *a, **k:
            (None, SimpleNamespace(channel=channel), None))
        with patch('transport.time.sleep', side_effect=lambda _: setattr(channel, 'tick', channel.tick + 1)):
            self.assertEqual(session.run('read catalogue'), 'catalogue rows\n')


if __name__ == '__main__':
    unittest.main()
