"""Local inspection and conversion. Original files are never modified."""
from dataclasses import dataclass, field
import hashlib
from pathlib import Path
import subprocess
import threading

import mutagen
from mutagen.mp4 import MP4, MP4Cover
from transport import DATA, RESOURCES

EXTENSIONS = {".mp3", ".m4a", ".aac", ".flac", ".wav", ".aif", ".aiff",
              ".ogg", ".oga", ".opus", ".wma", ".ape", ".mpc", ".alac", ".caf"}


class Cancelled(Exception):
    pass


def discover(paths):
    found = {}
    for value in paths:
        path = Path(value)
        candidates = path.rglob("*") if path.is_dir() else [path]
        for candidate in candidates:
            if candidate.is_file() and candidate.suffix.lower() in EXTENSIONS:
                found[str(candidate.resolve()).casefold()] = candidate.resolve()
    return sorted(found.values(), key=lambda p: str(p).casefold())


def _first(tags, *keys):
    for key in keys:
        try:
            value = tags.get(key)
        except (KeyError, ValueError, TypeError):
            continue
        if value is None:
            continue
        if hasattr(value, "text"):
            value = value.text
        if isinstance(value, (list, tuple)):
            if not value:
                continue
            value = value[0]
        return str(value)
    return ""


@dataclass
class Track:
    path: Path
    title: str
    artist: str
    album: str
    duration: float
    native: bool
    format: str
    number: int = 1
    total: int = 0
    year: int = 0
    genre: str = ""
    rate: int = 44100
    bits: int = 16
    channels: int = 2
    cover: bytes = field(default=b"", repr=False)
    digest: str = ""
    album_artist: str = ""
    copyright: str = ""

    @property
    def key(self):
        return (self.title.casefold(), self.artist.casefold(), self.album.casefold())


def inspect(path):
    audio = mutagen.File(path)
    if audio is None or not getattr(audio.info, "length", 0):
        raise ValueError("Не удалось прочитать аудиофайл: " + path.name)
    tags = audio.tags or {}
    title = _first(tags, "title", "\xa9nam", "TIT2") or path.stem
    artist = _first(tags, "artist", "\xa9ART", "TPE1", "Author") or "Unknown Artist"
    album = _first(tags, "album", "\xa9alb", "TALB", "WM/AlbumTitle") or path.parent.name
    number = _first(tags, "tracknumber", "TRCK", "WM/TrackNumber") or "1"
    total = _first(tags, "tracktotal", "totaltracks") or "0"
    if isinstance(audio, MP4):
        track = tags.get("trkn", [(1, 0)])[0]
        number, total = map(str, track)
    elif "/" in number:
        number, total = number.split("/", 1)
    year = _first(tags, "date", "year", "\xa9day", "TDRC", "TYER", "WM/Year")[:4]
    cover = b""
    if getattr(audio, "pictures", None):
        cover = audio.pictures[0].data
    elif tags.get("covr"):
        cover = bytes(tags["covr"][0])
    elif hasattr(tags, "getall") and tags.getall("APIC"):
        cover = tags.getall("APIC")[0].data
    if not cover:
        for name in ["cover.jpg", "folder.jpg", "cover.png", "Folder.jpg", "Cover.jpg"]:
            sidecar = path.parent / name
            if sidecar.is_file() and sidecar.stat().st_size < 15 * 1024 * 1024:
                cover = sidecar.read_bytes()
                break
    rate = int(getattr(audio.info, "sample_rate", 44100))
    bits = int(getattr(audio.info, "bits_per_sample", 24) or 24)
    channels = int(getattr(audio.info, "channels", 2))
    native = path.suffix.lower() in {".mp3", ".m4a", ".aac", ".wav", ".aif", ".aiff"}
    if isinstance(audio, MP4):
        codec = str(audio.info.codec).lower()
        native = codec == "alac" or codec.startswith("mp4a.40.") or codec == "aac"
    if rate > 48000 or channels > 2:
        native = False
    if path.suffix.lower() in {".wav", ".aif", ".aiff"}:
        native = bits <= 24 and rate <= 48000 and channels <= 2
    fmt = "ALAC" if isinstance(audio, MP4) and "alac" in str(audio.info.codec).lower() else path.suffix[1:].upper()
    return Track(path, title, artist, album, float(audio.info.length), native, fmt,
                 int(number) if number.isdigit() else 1,
                 int(total) if total.isdigit() and int(total) else 0,
                 int(year) if year.isdigit() else 0,
                 _first(tags, "genre", "\xa9gen", "TCON", "WM/Genre"),
                 rate, bits, channels, cover, album_artist=_first(tags, "albumartist", "aART", "TPE2", "WM/AlbumArtist") or artist,
                 copyright=_first(tags, "copyright", "\xa9cpy", "TCOP", "Copyright"))


def fingerprint(track, stop):
    digest = hashlib.sha256()
    with track.path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            if stop.is_set():
                raise Cancelled()
            digest.update(chunk)
    track.digest = digest.hexdigest()
    return track.digest


def ffmpeg():
    packaged = sorted(RESOURCES.glob("ffmpeg*.exe"))
    if packaged:
        return str(packaged[0])
    import imageio_ffmpeg
    return str(next((Path(imageio_ffmpeg.__file__).parent / "binaries").glob("ffmpeg*.exe")))


def convert(track, stop, progress):
    if track.native:
        return track.path
    cache = DATA / "cache"
    cache.mkdir(parents=True, exist_ok=True)
    target = cache / (track.digest + ".m4a")
    if target.exists():
        try:
            ready = MP4(target)
            if "alac" in ready.info.codec.lower() and abs(ready.info.length - track.duration) < 1:
                progress(1.0)
                return target
        except Exception:
            target.unlink(missing_ok=True)
    temporary = cache / (track.digest + ".part.m4a")
    command = [ffmpeg(), "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
               "-i", str(track.path), "-map", "0:a:0", "-vn", "-c:a", "alac",
               "-sample_fmt", "s16p" if track.bits <= 16 else "s32p", "-map_metadata", "0"]
    if track.rate > 48000:
        command += ["-ar", "44100" if track.rate % 44100 == 0 else "48000"]
    if track.channels > 2:
        command += ["-ac", "2"]
    command += ["-progress", "pipe:1", "-nostats", str(temporary)]
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True, encoding="utf-8", errors="replace",
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    try:
        for line in process.stdout:
            if stop.is_set():
                process.terminate()
                process.wait(timeout=10)
                raise Cancelled()
            if line.startswith("out_time_us="):
                try:
                    progress(min(.99, int(line.split("=", 1)[1]) / 1_000_000 / track.duration))
                except ValueError:
                    pass
        error = process.stderr.read()
        if process.wait() != 0:
            raise RuntimeError("Не удалось конвертировать " + track.path.name + ": " + error[-700:])
        converted = MP4(temporary)
        if abs(converted.info.length - track.duration) > 1:
            raise RuntimeError("Длительность после конвертации не совпала с исходником.")
        if track.cover:
            kind = MP4Cover.FORMAT_PNG if track.cover.startswith(b"\x89PNG") else MP4Cover.FORMAT_JPEG
            converted["covr"] = [MP4Cover(track.cover, imageformat=kind)]
            converted.save()
        temporary.replace(target)
        progress(1.0)
        return target
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        process.stdout.close()
        process.stderr.close()
        temporary.unlink(missing_ok=True)
