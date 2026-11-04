"""Transfer engine. Imports through Apple's library service in SpringBoard."""
from datetime import datetime, timedelta, timezone
import csv
import hashlib
import io
import json
from pathlib import Path
import plistlib
import posixpath
import re
import shlex
import threading
import time
import uuid

from media import Cancelled, convert, fingerprint, inspect
from transport import DATA, RESOURCES, Session

REMOTE = "/var/mobile/Media/iMetal"


def objc(value):
    return "@" + json.dumps(str(value), ensure_ascii=True)


class Bridge:
    def __init__(self, session):
        self.session = session
        self.pid = None

    def ensure(self):
        version = plistlib.loads(self.session.read("/System/Library/CoreServices/SystemVersion.plist"))
        if not version.get("ProductVersion", "").startswith("6."):
            raise RuntimeError("Эта версия iMetal подготовлена для iPod на iOS 6.")
        exists = self.session.run("if test -x /usr/bin/cycript; then echo yes; fi").strip()
        if exists != "yes":
            self.session.mkdir(REMOTE + "/Components")
            paths = []
            for path in sorted(RESOURCES.glob("*.deb")):
                remote = REMOTE + "/Components/" + path.name
                self.session.sftp.put(str(path), remote)
                paths.append(remote)
            if not paths:
                raise RuntimeError("На iPod не установлен компонент iMetal.")
            self.session.run("dpkg --install " + " ".join(map(shlex.quote, paths)), timeout=90)
        sqlite_helper = RESOURCES / "imetal-sqlite"
        try:
            installed = self.session.read("/usr/local/libexec/imetal-sqlite")
        except FileNotFoundError:
            installed = b""
        if hashlib.sha256(installed).digest() != hashlib.sha256(sqlite_helper.read_bytes()).digest():
            self.session.mkdir("/usr/local/libexec")
            self.session.sftp.put(str(sqlite_helper), "/usr/local/libexec/imetal-sqlite")
            self.session.sftp.chmod("/usr/local/libexec/imetal-sqlite", 0o755)
        self.session.mkdir(REMOTE + "/Incoming")
        self.session.run("chown mobile:mobile " + shlex.quote(REMOTE) + " " + shlex.quote(REMOTE + "/Incoming"))
        lines = self.session.run("ps ax").splitlines()
        self.pid = next((line.split()[0] for line in lines
                         if "/System/Library/CoreServices/SpringBoard.app/SpringBoard" in line), None)
        if not self.pid:
            raise RuntimeError("Не найден рабочий стол iPod. Разблокируйте устройство.")
        self.execute("""
dlopen("/System/Library/PrivateFrameworks/StoreServices.framework/StoreServices",2);
if(typeof imetalHeldDownload!=="undefined" && imetalHeldDownload){[imetalHeldDownload release];}
if(typeof imetalHeldMetadata!=="undefined" && imetalHeldMetadata){[imetalHeldMetadata release];}
if(typeof imetalQueue!=="undefined" && imetalQueue){[imetalQueue release];}
if(typeof imetalOptions!=="undefined" && imetalOptions){[imetalOptions release];}
var imetalOptions=[[SSDownloadManagerOptions alloc] init];
[imetalOptions setDownloadKinds:[SSDownloadQueue mediaDownloadKinds]];
[imetalOptions setShouldFilterExternalOriginatedDownloads:NO];
var imetalQueue=[[SSDownloadQueue alloc] initWithDownloadManagerOptions:imetalOptions];
[imetalQueue setShouldAutomaticallyFinishDownloads:YES];
var imetalHeldMetadata=null;
var imetalHeldDownload=null;
""")

    def execute(self, script, result=False):
        token = uuid.uuid4().hex
        path = REMOTE + "/" + token + ".cy"
        output = REMOTE + "/" + token + ".json"
        if result:
            script += "\n[[NSJSONSerialization dataWithJSONObject:imetalResult options:0 error:NULL] writeToFile:" + objc(output) + " atomically:YES];\n"
        self.session.write(path, script.encode("utf-8"))
        try:
            self.session.run("cycript -p " + self.pid + " " + shlex.quote(path), timeout=25)
            return json.loads(self.session.read(output)) if result else None
        finally:
            for remote in [path, output] if result else [path]:
                try:
                    self.session.sftp.remove(remote)
                except FileNotFoundError:
                    pass

    def library(self, title=None):
        return [r for r in self.entries(title) if r["asset"] and r["size"] > 0]

    def entries(self, title=None):
        sql = """SELECT CAST(i.item_pid AS TEXT),e.title,COALESCE(a.item_artist,''),
COALESCE(b.album,''),e.total_time_ms/1000.0,e.location,e.file_size,COALESCE(l.path,''),
CAST(e.date_created AS TEXT),CAST(e.store_item_id AS TEXT)
FROM item_extra e JOIN item i USING(item_pid)
LEFT JOIN item_artist a USING(item_artist_pid)
LEFT JOIN album b USING(album_pid)
LEFT JOIN base_location l USING(base_location_id)
WHERE 1=1"""
        if title is not None:
            sql += " AND e.title='" + title.replace("'", "''") + "'"
        command = shlex.join(["/usr/local/libexec/imetal-sqlite", "-csv",
                              "/var/mobile/Media/iTunes_Control/iTunes/MediaLibrary.sqlitedb", sql])
        data = self.session.run("su mobile -c " + shlex.quote(command), timeout=25)
        result = []
        for row in csv.reader(io.StringIO(data)):
            if len(row) != 10:
                raise RuntimeError("Не удалось прочитать каталог музыки на iPod.")
            pid, name, artist, album, duration, location, size, base, created, store_id = row
            asset = posixpath.normpath(posixpath.join("/var/mobile/Media", base, location)) if location else ""
            if asset and not asset.startswith("/var/mobile/Media/"):
                continue
            result.append({"pid": str(int(pid) & ((1 << 64) - 1)), "title": name,
                           "artist": artist, "album": album, "duration": float(duration or 0),
                           "asset": asset, "size": int(size), "created": created, "store_id": store_id})
        return result

    @staticmethod
    def placeholders(records, confirmed, previous_ids):
        """Only new, empty twins of this exact completed native import."""
        return [r for r in records if r["pid"] not in previous_ids
                and r["pid"] != confirmed["pid"] and not r["asset"] and r["size"] == 0
                and r.get("created") and r["created"] == confirmed.get("created")
                and r.get("store_id") == confirmed.get("store_id")
                and all(r[k] == confirmed[k] for k in ("title", "artist", "album"))
                and abs(r["duration"] - confirmed["duration"]) <= 2]

    def remove_empty_items(self, records):
        if not records:
            return
        if any(r["asset"] or r["size"] for r in records):
            raise ValueError("Удаление разрешено только для пустых записей загрузчика.")
        processes = self.session.run("ps ax").splitlines()
        music_pid = next((line.split()[0] for line in processes
                          if "/Applications/Music~iphone.app/Music~iphone" in line), None)
        if not music_pid:
            raise RuntimeError("Откройте «Музыку» на iPod для завершения импорта.")
        bridge = Bridge(self.session)
        bridge.pid = music_pid
        identifiers = ",".join(objc(r["pid"]) for r in records)
        titles = ",".join(objc(t) for t in sorted({r["title"] for r in records}))
        # Keep 64-bit identifiers as NSStrings throughout; JS numbers lose bits.
        script = ("var ipodsWanted=[NSSet setWithArray:@[" + identifiers + "]];\n"
                  + "var ipodsTitles=@[" + titles + "];\n") + """
var ipodsRemove=[NSMutableArray array];
for(var i=0;i<[ipodsTitles count];i++){
 // A shared songsQuery may cache the catalogue from before the upload.
 // Narrow the query, then compare exact ID strings without numeric conversion.
 var query=[MPMediaQuery songsQuery];
 [query addFilterPredicate:[MPMediaPropertyPredicate
  predicateWithValue:[ipodsTitles objectAtIndex:i] forProperty:@"title"]];
 var items=[query items];
 for(var j=0;j<[items count];j++){
  var it=[items objectAtIndex:j];
  if([ipodsWanted containsObject:[[it valueForProperty:@"persistentID"] description]]
     && ![it valueForProperty:@"assetURL"]) [ipodsRemove addObject:it];
 }
}
var ipodsOK=NO;
if([ipodsRemove count]==[ipodsWanted count])
 ipodsOK=[[MPMediaLibrary defaultMediaLibrary] removeItems:ipodsRemove];
var imetalResult=@{@"removed":[NSNumber numberWithBool:ipodsOK],
 @"count":[NSNumber numberWithUnsignedInt:[ipodsRemove count]]};
"""
        result = bridge.execute(script, result=True)
        if not result.get("removed") or result.get("count") != len(records):
            raise RuntimeError("Не удалось завершить очистку записей загрузчика iPod.")

    def finish_imports(self, imports, last_add):
        if not imports:
            return
        # Music inserts its placeholder asynchronously, several seconds AFTER
        # StoreServices has installed the asset. Checking only the asset misses it.
        time.sleep(max(0, last_add + 6 - time.monotonic()))
        records = self.entries()
        by_pid = {r["pid"]: r for r in records}
        remove = {}
        for confirmed, previous_ids in imports:
            current = by_pid.get(confirmed["pid"])
            if not current or current["asset"] != confirmed["asset"] or current["size"] != confirmed["size"]:
                raise RuntimeError("Каталог iPod изменился во время завершения импорта.")
            if self.session.sftp.stat(current["asset"]).st_size != current["size"]:
                raise RuntimeError("Не найден полный файл импортированного трека.")
            for r in self.placeholders(records, current, previous_ids):
                remove[r["pid"]] = r
        if not remove:
            return
        # Preserve the exact empty records before asking Apple's API to remove them.
        audit = {"removed": list(remove.values()), "retained": [r for r, _ in imports]}
        directory = DATA / "cleanup"
        directory.mkdir(parents=True, exist_ok=True)
        (directory / (uuid.uuid4().hex + ".json")).write_text(
            json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
        self.remove_empty_items(list(remove.values()))
        remaining = {r["pid"] for r in self.entries()}
        if remaining.intersection(remove) or any(r["pid"] not in remaining for r, _ in imports):
            raise RuntimeError("iPod не подтвердил завершение очистки загрузчика.")
        for r, _ in imports:
            if self.session.sftp.stat(r["asset"]).st_size != r["size"]:
                raise RuntimeError("Файл трека недоступен после завершения импорта.")

    @staticmethod
    def match(records, track):
        return next((r for r in records
                     if (r["title"].casefold(), r["artist"].casefold(), r["album"].casefold()) == track.key
                     and r.get("asset", "").startswith("/var/mobile/Media/")
                     and abs(float(r.get("duration", 0)) - track.duration) <= 2), None)

    def add(self, track, remote, cover=None):
        fields = {
            "setTitle:": objc(track.title),
            "setArtistName:": objc(track.artist),
            "setCollectionArtistName:": objc(track.album_artist or track.artist),
            "setCollectionName:": objc(track.album),
            "setKind:": objc("song"),
            "setDurationInMilliseconds:": "@" + str(round(track.duration * 1000)),
            "setIndexInCollection:": "@" + str(track.number),
        }
        if track.total:
            fields["setCollectionItemCount:"] = "@" + str(track.total)
        if track.year:
            fields["setReleaseYear:"] = "@" + str(track.year)
        if track.genre:
            fields["setGenre:"] = objc(track.genre)
        if track.copyright:
            fields["setCopyright:"] = objc(track.copyright)
        script = """
if(imetalHeldDownload){[imetalHeldDownload release];imetalHeldDownload=null;}
if(imetalHeldMetadata){[imetalHeldMetadata release];imetalHeldMetadata=null;}
imetalHeldMetadata=[[SSDownloadMetadata alloc] initWithDictionary:@{@"is-in-queue":@0}];
[imetalHeldMetadata setPurchaseDate:[NSDate date]];
[imetalHeldMetadata setReleaseDate:[NSDate date]];
"""
        script += "[imetalHeldMetadata setPrimaryAssetURL:[NSURL fileURLWithPath:" + objc(remote) + "]];\n"
        for selector, value in fields.items():
            script += f"if([imetalHeldMetadata respondsToSelector:@selector({selector})]) [imetalHeldMetadata {selector}{value}];\n"
        if cover:
            script += "[imetalHeldMetadata setFullSizeImageURL:[NSURL fileURLWithPath:" + objc(cover) + "]];\n"
        script += """
imetalHeldDownload=[[SSDownload alloc] initWithDownloadMetadata:imetalHeldMetadata];
[imetalQueue addDownload:imetalHeldDownload];
"""
        self.execute(script)

    def failure(self, remote, cancel=False):
        script = """
var imetalResult=[NSMutableArray array];
var imetalCurrentDownloads=[imetalQueue downloads];
for(var i=0;i<[imetalCurrentDownloads count];i++){
 var d=[imetalCurrentDownloads objectAtIndex:i];
 var m=[d metadata];
 var u=[m primaryAssetURL];
"""
        script += "if(u && [[u path] isEqualToString:" + objc(remote) + "]) {\n"
        script += """
 var phase=[d downloadPhaseIdentifier];
 var error=[d failureError];
 [imetalResult addObject:@{@"phase":phase||@"",@"error":error?[error localizedDescription]:@""}];
"""
        if cancel:
            script += '[imetalQueue cancelDownload:d];\n'
        script += "}}\n"
        return self.execute(script, result=True)


class Engine:
    def __init__(self, config, emit=lambda event: None, stop=None):
        self.config = config
        self.emit = emit
        self.stop = stop or threading.Event()
        DATA.mkdir(parents=True, exist_ok=True)

    def check(self):
        with Session(self.config) as session:
            return session.info()

    def transfer(self, paths):
        from device_lock import device_lock
        with device_lock(self.config):
            return self._transfer(paths)

    def _transfer(self, paths):
        paths = list({str(Path(p).resolve()).casefold(): Path(p).resolve() for p in paths}.values())
        imported, skipped, errors = 0, 0, 0
        total = len(paths)
        if not total:
            return {"imported": 0, "skipped": 0, "errors": 0}
        sizes = {str(Path(p)): Path(p).stat().st_size for p in paths}
        estimated = {key for key in sizes if Path(key).suffix.lower() not in (".mp3", ".m4a", ".aac", ".wav", ".aif", ".aiff")}
        uploaded_bytes = 0
        current_uploaded = [0]
        def counters():
            return {"tracks_total": total, "imported": imported, "skipped": skipped, "errors": errors,
                    "bytes_transferred": uploaded_bytes + current_uploaded[0],
                    "bytes_total": sum(sizes.values()), "bytes_estimated": bool(estimated)}
        with Session(self.config) as session:
            self.emit({"type": "connection", **session.info()})
            self.emit({"stage": "Подготавливаем iPod", "overall": 0, "detail": "Проверяем подключение и компонент импорта…", **counters()})
            bridge = Bridge(session)
            bridge.ensure()
            library = bridge.library()
            imports = []
            last_add = 0
            try:
                for index, path in enumerate(paths):
                    path = Path(path)
                    uploaded_bytes += current_uploaded[0]
                    current_uploaded[0] = 0
                    if self.stop.is_set():
                        return {"cancelled": True, **counters()}
                    track = None
                    remote = None
                    current = {}
                    def event(fraction, stage, state=None, detail=None):
                        payload = {"path": str(path), "overall": (index + fraction) / total,
                                   "stage": stage, "title": track.title if track else path.stem,
                                   "detail": detail or f"Трек {index + 1} из {total} · {stage}", **counters()}
                        if state:
                            payload["state"] = state
                        if track:
                            payload["format"] = track.format if track.native else track.format + " → ALAC"
                        self.emit(payload)
                    try:
                        event(0, "Читаем информацию", "Подготовка")
                        track = inspect(path)
                        if track.native:
                            estimated.discard(str(path))
                        else:
                            estimated.add(str(path))
                        event(.03, "Подготовка", "Подготовка", f"{track.artist} — {track.album}")
                        entries = bridge.entries(track.title)
                        known = bridge.match(entries, track)
                        if known:
                            # A previous interrupted finalization may have left an
                            # empty twin. Repair it without re-enqueueing the song.
                            imports.append((known, set()))
                            skipped += 1
                            sizes[str(path)] = 0
                            estimated.discard(str(path))
                            event(1, "Трек уже на iPod", "Уже на iPod")
                            continue
                        if any(not r["asset"] and not r["size"]
                               and (r["title"].casefold(), r["artist"].casefold(), r["album"].casefold()) == track.key
                               and abs(r["duration"] - track.duration) <= 2 for r in entries):
                            raise RuntimeError("На iPod осталась незавершённая загрузка этого трека.")
                        fingerprint(track, self.stop)
                        local = convert(track, self.stop, lambda value: event(.06 + .29 * value, "Конвертация в ALAC", "Конвертация"))
                        sizes[str(path)] = local.stat().st_size
                        estimated.discard(str(path))
                        if self.stop.is_set():
                            raise Cancelled()
                        if session.info()["free"] < local.stat().st_size + 4 * 1024 * 1024:
                            raise RuntimeError("На iPod недостаточно свободного места для этого трека.")
                        remote = REMOTE + "/Incoming/" + track.digest + local.suffix.lower()
                        partial = remote + ".part"
                        event(.36, "Загрузка на iPod", "Загрузка")
                        def uploaded(done, size):
                            current_uploaded[0] = done
                            if self.stop.is_set():
                                raise Cancelled()
                            event(.36 + .44 * done / max(size, 1), "Загрузка на iPod", "Загрузка",
                                  f"{track.title} · {done / 1048576:.1f} / {size / 1048576:.1f} МБ · {session.mode}")
                        try:
                            session.sftp.put(str(local), partial, callback=uploaded)
                            session.sftp.chmod(partial, 0o644)
                            session.sftp.posix_rename(partial, remote)
                            uploaded_bytes += local.stat().st_size
                            current_uploaded[0] = 0
                        except BaseException:
                            try:
                                session.sftp.remove(partial)
                            except OSError:
                                pass
                            raise
                        local_digest = hashlib.sha256(local.read_bytes()).hexdigest()
                        checksum = session.run("openssl dgst -sha256 " + shlex.quote(remote)).strip().split()[-1]
                        if checksum.lower() != local_digest:
                            raise RuntimeError("Контрольная сумма после передачи не совпала. Повторите перенос.")
                        cover = self.cover(session, track)
                        session.run("chown mobile:mobile " + shlex.quote(remote))
                        event(.83, "Добавляем в «Музыку»", "Добавление")
                        previous_ids = {r["pid"] for r in bridge.entries(track.title)}
                        bridge.add(track, remote, cover)
                        last_add = time.monotonic()
                        deadline = time.monotonic() + 50
                        confirmed = None
                        while time.monotonic() < deadline:
                            time.sleep(.8)
                            records = bridge.library(track.title)
                            confirmed = bridge.match(records, track)
                            if confirmed:
                                break
                            state = bridge.failure(remote)
                            failed = next((s for s in state if s["phase"] == "SSDownloadPhaseFailed"), None)
                            if failed:
                                bridge.failure(remote, cancel=True)
                                raise RuntimeError(failed["error"] or "Загрузчик iPod отклонил файл.")
                            event(.9, "Проверяем библиотеку", "Проверка")
                        if not confirmed:
                            raise RuntimeError("iPod ещё не подтвердил добавление. Повторите: готовые треки не дублируются.")
                        physical = session.sftp.stat(confirmed["asset"])
                        if physical.st_size != local.stat().st_size:
                            raise RuntimeError("Размер файла в библиотеке не совпал с отправленным.")
                        final_digest = session.run("openssl dgst -sha256 " + shlex.quote(confirmed["asset"])).strip().split()[-1]
                        if final_digest.lower() != local_digest:
                            raise RuntimeError("Файл в библиотеке отличается от отправленного.")
                        library.append(confirmed)
                        imports.append((confirmed, previous_ids))
                        imported += 1
                        self.history(track, confirmed, session.mode)
                        if not track.native:
                            local.unlink(missing_ok=True)
                        event(1, "Трек добавлен в «Музыку»", "На iPod")
                    except Cancelled:
                        event(0, "Перенос остановлен", "Остановлено")
                        return {"cancelled": True, **counters()}
                    except Exception as error:
                        errors += 1
                        message = str(error) or "Не удалось обработать файл."
                        event(1, "Не удалось добавить трек", "Ошибка", message)
                        with (DATA / "errors.log").open("a", encoding="utf-8") as log:
                            log.write(f"{datetime.now().isoformat()} {path.name}: {error}\n")
                        if not session.client.get_transport() or not session.client.get_transport().is_active():
                            raise RuntimeError("Связь с iPod прервалась. Подключите его и повторите перенос.") from error
            finally:
                if imports:
                    self.emit({"stage": "Завершаем импорт", "overall": 1, **counters()})
                    bridge.finish_imports(imports, last_add)
            self.emit({"type": "connection", **session.info()})
        return counters()

    def cover(self, session, track):
        if not track.cover:
            return None
        from PySide6.QtGui import QImage
        image = QImage.fromData(track.cover)
        if image.isNull():
            return None
        cache = DATA / "cache"
        cache.mkdir(exist_ok=True)
        path = cache / (track.digest + "-cover.jpg")
        if image.width() > 1200 or image.height() > 1200:
            from PySide6.QtCore import Qt
            image = image.scaled(1200, 1200, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        if not image.save(str(path), "JPEG", 94):
            return None
        remote = REMOTE + "/Incoming/" + path.name
        session.sftp.put(str(path), remote)
        session.sftp.chmod(remote, 0o644)
        path.unlink(missing_ok=True)
        return remote

    def history(self, track, confirmed, mode):
        path = DATA / "history.json"
        try:
            history = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError):
            history = []
        history.append({"title": track.title, "artist": track.artist, "album": track.album,
                        "digest": track.digest, "pid": confirmed["pid"],
                        "format": track.format if track.native else "ALAC", "mode": mode,
                        "date": datetime.now(timezone(timedelta(hours=3))).strftime("%d.%m %H:%M")})
        temporary = path.with_suffix(".part")
        temporary.write_text(json.dumps(history[-2000:], ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)
