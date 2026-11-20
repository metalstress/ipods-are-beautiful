"""Host-specific paths and process flags; no interface or device policy."""
import os
from pathlib import Path
import socket
import subprocess
import sys


def data_directory(platform=None, home=None, environ=None):
    platform = platform or sys.platform
    home = Path(home) if home is not None else Path.home()
    environ = os.environ if environ is None else environ
    if platform == 'win32':
        return Path(environ.get('LOCALAPPDATA', home / 'AppData/Local')) / 'iMetal'
    if platform == 'darwin':
        return home / 'Library/Application Support/iMetal'
    return Path(environ.get('XDG_DATA_HOME', home / '.local/share')) / 'iMetal'


def executable_name(name, platform=None):
    return name + ('.exe' if (platform or sys.platform) == 'win32' else '')


def process_flags():
    return getattr(subprocess, 'CREATE_NO_WINDOW', 0) if sys.platform == 'win32' else 0


def usbmux_connection(timeout):
    if sys.platform == 'win32':
        return socket.create_connection(('127.0.0.1', 27015), timeout=timeout)
    connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    connection.settimeout(timeout)
    try:
        connection.connect('/var/run/usbmuxd')
        return connection
    except BaseException:
        connection.close()
        raise


def font_directories():
    if sys.platform == 'darwin':
        return [Path('/System/Library/Fonts/Supplemental'), Path('/System/Library/Fonts'),
                Path('/Library/Fonts'), Path.home() / 'Library/Fonts']
    return [Path(os.environ.get('WINDIR', r'C:\Windows')) / 'Fonts',
            Path(os.environ.get('LOCALAPPDATA', Path.home()/'AppData/Local')) / 'Microsoft/Windows/Fonts']
