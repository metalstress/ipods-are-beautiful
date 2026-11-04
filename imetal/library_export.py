"""Verified local export sink. Never overwrite a file already on the PC."""
import hashlib
import os
from pathlib import Path, PurePosixPath
import re
import tempfile

from library_model import LibraryError


def filename(track):
    title = re.sub(r'[\x00-\x1f<>:"/\\|?*]', '_', track['title']).strip(' .')[:150] or 'Track'
    if title.split('.')[0].upper() in {'CON', 'PRN', 'AUX', 'NUL', *(f'COM{i}' for i in range(1, 10)),
                                      *(f'LPT{i}' for i in range(1, 10))}:
        title = '_' + title
    number = f"{track['track_number']:02d} - " if track['track_number'] else ''
    extension = PurePosixPath(track['asset_path']).suffix.lower()
    if not re.fullmatch(r'\.[a-z0-9]{1,8}', extension):
        raise LibraryError('invalid_asset', 'The audio asset has no supported file extension.')
    return number + title + extension


class ExportFile:
    def __init__(self, destination, track):
        directory = Path(destination)
        if not directory.is_absolute():
            raise LibraryError('invalid_destination', 'Choose an absolute export folder.')
        directory.mkdir(parents=True, exist_ok=True)
        self.directory = directory.resolve()
        self.track = track
        self.file = tempfile.NamedTemporaryFile(prefix='.ipods-', suffix='.part', dir=self.directory, delete=False)
        self.path = Path(self.file.name)
        self.hash = hashlib.sha256()
        self.size = 0

    def write(self, data):
        self.file.write(data)
        self.hash.update(data)
        self.size += len(data)

    def finish(self):
        self.file.flush()
        os.fsync(self.file.fileno())
        self.file.close()
        if self.size != self.track['size_bytes']:
            raise LibraryError('asset_changed', 'Audio file size differs from the library snapshot.')
        with self.path.open('rb') as source:
            digest = hashlib.file_digest(source, 'sha256').hexdigest()
        if digest != self.hash.hexdigest():
            raise LibraryError('export_verification_failed', 'The exported file failed verification.')
        name = filename(self.track)
        stem, suffix = Path(name).stem, Path(name).suffix
        # A hard link publishes an already-complete file atomically. Unlike a
        # check-then-rename, it cannot overwrite an existing destination in a race.
        for index in range(10000):
            target = self.directory / (name if index == 0 else f'{stem} ({index + 1}){suffix}')
            try:
                os.link(self.path, target)
                self.path.unlink()
                return {'track_id': self.track['id'], 'path': str(target), 'bytes': self.size, 'sha256': digest}
            except FileExistsError:
                continue
            except OSError as error:
                raise LibraryError('export_filesystem', 'This folder does not support atomic file export.') from error
        raise LibraryError('export_name_conflict', 'No free export filename is available.')

    def close(self):
        self.file.close()
        self.path.unlink(missing_ok=True)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
