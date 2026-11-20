"""GUI thread wrapper; all device work stays in the headless child process."""
import base64
import threading

from PySide6.QtCore import QThread, Signal

from backend_client import run_backend


class ArtworkJob(QThread):
    event = Signal(dict)
    result = Signal(dict)

    def __init__(self, info, excluded):
        super().__init__()
        self.device_id = info['id']
        self.config = dict(info['config'])
        self.excluded = list(excluded)
        self.stop = threading.Event()

    def run(self):
        try:
            result = run_backend('library.repair_artwork', self.config, stop=self.stop,
                                 payload={'exclude_album_ids': self.excluded})
            encoded = result.pop('artwork', None)
            if encoded:
                data = base64.b64decode(encoded, validate=True)
                result['changed_covers'] = dict.fromkeys(result.get('track_ids', []), data)
            self.result.emit(result)
        except Exception as error:
            self.result.emit({'error': str(error) or type(error).__name__})
