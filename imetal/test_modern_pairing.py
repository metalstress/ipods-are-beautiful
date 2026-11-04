from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock,patch

from pymobiledevice3.exceptions import GetProhibitedError
from modern_pairing import connect_paired_wifi


class PairedWifiTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.calls=[]
        self.tls=False
        self.require_tls=True
        self.identity='test-phone'
        parent=self
        async def start_tls(context):
            parent.calls.append('TLS');parent.tls=True
        self.service=SimpleNamespace(start_tls=AsyncMock(side_effect=start_tls))
        class Client:
            def __init__(self,*args,**kwargs):
                self.paired=False
                self.close=AsyncMock()
                parent.client=self
            async def query_type(self):
                parent.calls.append('QueryType');return 'com.apple.mobile.lockdown'
            async def _request(self,command,params):
                parent.calls.append(command)
                return {'SessionID':'test-session','EnableSessionSSL':parent.require_tls}
            @contextmanager
            def ssl_file(self):yield 'test-only.pem'
            async def get_value(self):
                parent.calls.append('GetValue')
                if not parent.tls:raise GetProhibitedError()
                return {'UniqueDeviceID':parent.identity,'ProductType':'iPhone17,1'}
        self.patches=[patch('modern_pairing.ServiceConnection.create_using_tcp',AsyncMock(return_value=self.service)),
                      patch('modern_pairing.TcpLockdownClient',Client),
                      patch('modern_pairing.create_ssl_context',return_value=object()),
                      patch('modern_pairing.get_home_folder',return_value=Path('.'))]
        for p in self.patches:p.start()
        self.record={'HostID':'test-host','SystemBUID':'test-buid'}

    def tearDown(self):
        for p in reversed(self.patches):p.stop()

    async def test_prohibited_unauthenticated_values_are_never_requested(self):
        result=await connect_paired_wifi('192.0.2.1','test-phone',self.record)
        self.assertEqual(self.calls,['QueryType','StartSession','TLS','GetValue'])
        self.assertTrue(result.paired)

    async def test_refuses_plaintext_session_and_closes_it(self):
        self.require_tls=False
        with self.assertRaisesRegex(RuntimeError,'TLS'):
            await connect_paired_wifi('192.0.2.1','test-phone',self.record)
        self.assertNotIn('GetValue',self.calls)
        self.client.close.assert_awaited_once()

    async def test_rejects_another_phone_before_exposing_it_as_connected(self):
        self.identity='another-device'
        with self.assertRaisesRegex(RuntimeError,'identity differs'):
            await connect_paired_wifi('192.0.2.1','test-phone',self.record)
        self.client.close.assert_awaited_once()


if __name__=='__main__':unittest.main()
