"""Process-wide device-operation exclusion shared by imports and library jobs."""
from contextlib import contextmanager
import hashlib
import os

from transport import DATA


@contextmanager
def device_lock(config):
    identity = config.get('udid') if config.get('backend') == 'modern' else config.get('host_key')
    key = hashlib.sha256((identity or 'unconfigured-device').encode()).hexdigest()
    root = DATA / 'device-locks'
    root.mkdir(parents=True, exist_ok=True)
    handle = (root / (key + '.lock')).open('a+b')
    acquired = False
    try:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b'\0');handle.flush()
        handle.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            acquired = True
        except OSError as error:
            from library_model import LibraryError
            raise LibraryError('device_busy', 'Another operation is using this device.') from error
        yield
    finally:
        if acquired:
            handle.seek(0)
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_UN)
        handle.close()
