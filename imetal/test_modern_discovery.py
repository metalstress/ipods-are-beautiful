import asyncio
import unittest
from unittest.mock import AsyncMock,patch

from modern_devices import discover_modern


class DiscoveryTests(unittest.TestCase):
    def test_usb_to_wifi_transition_survives_first_connection_timeout(self):
        profile={'udid':'phone-test','host':'192.0.2.10','service':'test._apple-mobdev2._tcp.local.'}
        phone={'id':'phone-test','kind':'iphone','mode':'Wi-Fi'}
        probe=AsyncMock(side_effect=[TimeoutError(),phone])
        with patch('modern_devices.load_profiles',return_value=[profile]), \
             patch('modern_devices.save_profiles') as save, \
             patch('modern_devices.inspect_phone',probe), \
             patch('modern_devices.record_failure'), \
             patch('pymobiledevice3.bonjour.browse_mobdev2',AsyncMock(return_value=[])), \
             patch('modern_devices.asyncio.sleep',AsyncMock()):
            result=asyncio.run(discover_modern({'mode':'wifi','ip':'192.0.2.99'}))
        self.assertEqual(result,[phone])
        self.assertEqual(probe.await_count,2)
        self.assertTrue(all(c.args[1]=='192.0.2.10' for c in probe.await_args_list))
        save.assert_called_once()

    def test_wifi_inspection_does_not_read_protected_wireless_settings(self):
        from types import SimpleNamespace
        from modern_devices import inspect_phone
        client=SimpleNamespace(all_values={'DeviceName':'Phone','ProductType':'iPhone17,1','ProductVersion':'27.0'},
                               get_value=AsyncMock(),close=AsyncMock())
        with patch('modern_music.ModernDevice') as full_connection, \
             patch('modern_pairing.connect_paired_wifi',AsyncMock(return_value=client)), \
             patch('modern_devices.pair_record',AsyncMock(return_value={})):
            info=asyncio.run(inspect_phone({'udid':'test-phone'},'192.0.2.1'))
        self.assertEqual(info['mode'],'Wi-Fi')
        client.get_value.assert_not_awaited()
        client.close.assert_awaited_once()
        full_connection.assert_not_called()

    def test_bonjour_presence_shows_card_without_opening_a_transfer_connection(self):
        from types import SimpleNamespace
        profile={'udid':'phone-test','service':'test._apple-mobdev2._tcp.local.',
                 'product_type':'iPhone17,1','product_version':'27.0','name':'Phone',
                 'free':100,'total':200}
        service=SimpleNamespace(instance=profile['service'],addresses=[SimpleNamespace(ip='192.0.2.10')])
        with patch('modern_devices.load_profiles',return_value=[profile]), \
             patch('modern_devices.save_profiles'), \
             patch('modern_devices.inspect_phone',AsyncMock()) as probe, \
             patch('pymobiledevice3.bonjour.browse_mobdev2',AsyncMock(return_value=[service])):
            result=asyncio.run(discover_modern({'mode':'wifi'}))
        self.assertEqual(len(result),1)
        self.assertEqual(result[0]['mode'],'Wi-Fi')
        self.assertEqual(result[0]['free'],100)
        probe.assert_not_awaited()


if __name__=='__main__':unittest.main()
