import hashlib
import json
from pathlib import Path
import struct
import subprocess
import tempfile
import threading
import unittest
import wave

from mutagen.flac import FLAC
from engine import Bridge, objc
from media import Track, Cancelled, discover, inspect, fingerprint, convert, ffmpeg
from transport import DATA


class CoreTests(unittest.TestCase):
    def test_discovery_filters_and_deduplicates_nested_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            album = folder / "album"
            album.mkdir()
            (album / "01.mp3").write_bytes(b"audio")
            (album / "cover.jpg").write_bytes(b"not audio")
            (album / "02.flac.part").write_bytes(b"incomplete")
            found = discover([folder, album, album / "01.mp3"])
            self.assertEqual(found, [album / "01.mp3"])

    def test_pending_library_entries_are_not_success(self):
        track = Track(Path("a.flac"), "Track", "Artist", "Album", 10, False, "FLAC")
        pending = {"title": "Track", "artist": "Artist", "album": "Album",
                   "duration": 10, "asset": "", "pid": "1"}
        self.assertIsNone(Bridge.match([pending], track))
        ready = {**pending, "asset": "/var/mobile/Media/iTunes_Control/Music/F00/a.m4a"}
        self.assertIs(Bridge.match([pending, ready], track), ready)
        self.assertIsNone(Bridge.match([{**ready, "duration": 90}], track))

    def test_metadata_cannot_escape_objective_c_string(self):
        text = 'Песня "x"; system("bad");\n\\'
        value = objc(text)
        self.assertEqual(json.loads(value[1:]), text)
        self.assertNotIn("\n", value)

    def test_cancel_during_fingerprint(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "large.mp3"
            source.write_bytes(b"x" * 100)
            track = Track(source, "T", "A", "B", 1, True, "MP3")
            stop = threading.Event()
            stop.set()
            with self.assertRaises(Cancelled):
                fingerprint(track, stop)

    def test_flac_to_alac_preserves_pcm_and_original(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            wav = folder / "source.wav"
            with wave.open(str(wav), "wb") as out:
                out.setnchannels(2)
                out.setsampwidth(2)
                out.setframerate(44100)
                out.writeframes(b"".join(struct.pack("<hh", i % 20000, -(i % 20000)) for i in range(4410)))
            flac = folder / "source.flac"
            subprocess.run([ffmpeg(), "-v", "error", "-i", str(wav), str(flac)],
                           check=True, creationflags=subprocess.CREATE_NO_WINDOW)
            audio = FLAC(flac)
            audio["title"] = 'Песня "один"'
            audio["artist"] = "Исполнитель"
            audio["album"] = "Альбом"
            audio["tracknumber"] = "2/3"
            audio.save()
            original = hashlib.sha256(flac.read_bytes()).hexdigest()
            track = inspect(flac)
            self.assertEqual((track.title, track.number, track.total), ('Песня "один"', 2, 3))
            stop = threading.Event()
            fingerprint(track, stop)
            target = convert(track, stop, lambda p: None)
            def pcm(path):
                return subprocess.check_output([ffmpeg(), "-v", "error", "-i", str(path),
                    "-map", "0:a:0", "-c:a", "pcm_s16le", "-f", "md5", "-"],
                    creationflags=subprocess.CREATE_NO_WINDOW)
            try:
                self.assertEqual(pcm(flac), pcm(target))
                self.assertEqual(hashlib.sha256(flac.read_bytes()).hexdigest(), original)
                ready = inspect(target)
                self.assertTrue(ready.native)
                self.assertEqual(ready.format, "ALAC")
                self.assertEqual((ready.title, ready.artist, ready.album),
                                 (track.title, track.artist, track.album))
            finally:
                target.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
