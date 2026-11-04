import asyncio
import unittest
from unittest.mock import AsyncMock
from modern_music import ModernDevice


class MusicKeepaliveTests(unittest.IsolatedAsyncioTestCase):
    async def test_ping_is_answered_even_while_audio_body_is_busy(self):
        d=ModernDevice('test-phone','.')
        stalled=asyncio.Event()
        calls=[{'Command':'Ping','Session':2}]
        async def frame(timeout):
            if calls:return calls.pop()
            await stalled.wait()
        d.read_frame=frame;d.send=AsyncMock()
        d.start_reader()
        try:
            # No foreground receive call: the body may still be uploading.
            await asyncio.sleep(.01)
            d.send.assert_awaited_once_with('Pong',session=2)
            self.assertIsNone(d.reader_error)
        finally:await d.stop_reader()

    async def test_handshake_skips_ping_and_preserves_the_next_message(self):
        d=ModernDevice('test-phone','.')
        progress={'Command':'Progress','Session':2}
        d.read_frame=AsyncMock(side_effect=[{'Command':'Ping','Session':7},progress])
        d.send=AsyncMock()
        self.assertEqual(await d.receive(),progress)
        d.send.assert_awaited_once_with('Pong',session=7)

    async def test_background_connection_error_reaches_the_foreground(self):
        d=ModernDevice('test-phone','.')
        d.read_frame=AsyncMock(side_effect=ConnectionError('peer disconnected'))
        d.start_reader()
        try:
            with self.assertRaisesRegex(ConnectionError,'peer disconnected'):
                await d.receive()
        finally:await d.stop_reader()

    async def test_terminal_success_is_not_replaced_by_following_socket_eof(self):
        d=ModernDevice('test-phone','.')
        d.completion_sent=True
        finished={'Command':'SyncFinished','Session':2}
        d.read_frame=AsyncMock(side_effect=[finished,ConnectionError('normal EOF')])
        d.start_reader()
        try:
            await asyncio.sleep(0)
            self.assertEqual(await d.receive(),finished)
            self.assertIsNone(d.reader_error)
            self.assertEqual(d.read_frame.await_count,1)
        finally:await d.stop_reader()


if __name__=='__main__':unittest.main()
