from pathlib import Path
from unittest import TestCase
from unittest.mock import Mock, patch

from host_platform import data_directory, executable_name, process_flags, usbmux_connection


class HostPlatformTests(TestCase):
    def test_macos_data_does_not_use_windows_appdata(self):
        home=Path('/Users/test')
        self.assertEqual(data_directory('darwin',home,{'LOCALAPPDATA':'wrong'}),home/'Library/Application Support/iMetal')

    def test_existing_windows_settings_location_is_preserved(self):
        self.assertEqual(data_directory('win32',Path('/test'),{'LOCALAPPDATA':'local'}),Path('local/iMetal'))

    def test_native_helpers_use_the_host_extension(self):
        self.assertEqual(executable_name('signer','darwin'),'signer')
        self.assertEqual(executable_name('signer','win32'),'signer.exe')

    def test_macos_process_flags_do_not_request_windows_creation_options(self):
        with patch('host_platform.sys.platform','darwin'):
            self.assertEqual(process_flags(),0)

    def test_macos_legacy_usb_uses_the_system_unix_socket(self):
        with patch('host_platform.sys.platform','darwin'),patch('host_platform.socket.AF_UNIX',1,create=True),patch('host_platform.socket.socket') as factory:
            connection=usbmux_connection(3)
        factory.return_value.connect.assert_called_once_with('/var/run/usbmuxd')
        factory.return_value.settimeout.assert_called_once_with(3)
        self.assertIs(connection,factory.return_value)

    def test_failed_unix_connection_is_closed(self):
        with patch('host_platform.sys.platform','darwin'),patch('host_platform.socket.AF_UNIX',1,create=True),patch('host_platform.socket.socket') as factory:
            factory.return_value.connect.side_effect=OSError('not available')
            with self.assertRaises(OSError):usbmux_connection(3)
        factory.return_value.close.assert_called_once()

    def test_windows_legacy_usb_keeps_the_apple_service_port(self):
        with patch('host_platform.sys.platform','win32'),patch('host_platform.socket.create_connection') as connect:
            usbmux_connection(5)
        connect.assert_called_once_with(('127.0.0.1',27015),timeout=5)
