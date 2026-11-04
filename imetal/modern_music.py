"""Windows native Music adapter, based on rustunes' MIT ATC protocol research.

Original signer: ../rustunes, commit 057ee8fc9c9da65795a6f14497fc408c2fe23c76.
No database is written on the phone. Library IDs and pairing keys are not logged.
"""
import asyncio
from contextlib import AsyncExitStack, suppress
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import plistlib
import secrets
import sqlite3
import struct
import subprocess
import time
from media import inspect as inspect_track, ffmpeg, Cancelled
from mutagen.mp4 import MP4, MP4Cover
from mutagen.mp3 import MP3
import mutagen
from transport import DATA, RESOURCES
from pending_imports import interrupted_imports

from pymobiledevice3.exceptions import AfcFileNotFoundError
from pymobiledevice3.lockdown import create_using_usbmux
from modern_pairing import connect_paired_wifi
from pymobiledevice3.services.afc import AfcService

SIGNER = RESOURCES/'ipods-music-signer.exe'
MUSIC_DIR = '/iTunes_Control/iTunes'
STAGING = '/iTunes_Control/Sync/Media'
OPERATION = STAGING + '/Sync_00000000.plist'
SIGNATURE = OPERATION + '.cig'
MAX_FRAME = 16 * 1024 * 1024


def native_sign(command, data=None):
    if not SIGNER.is_file():
        raise RuntimeError('The Music signing component is missing. Reinstall this build.')
    result = subprocess.run([str(SIGNER), command], input=data, capture_output=True,
                            creationflags=subprocess.CREATE_NO_WINDOW, timeout=15)
    if result.returncode:
        raise RuntimeError(result.stderr.decode(errors='replace')[-300:])
    return result.stdout


def library_identity(data):
    raw = data if data.startswith(b'frpd') else plistlib.loads(data).get('iPodPrefs', b'')
    if len(raw) != 1232 or raw[:4] != b'frpd' or raw[4:8] != b'\x01\x00\x19\x00' or raw[8] != 1:
        raise RuntimeError('Unsupported or unconfigured existing Music identity')
    catalog, user = struct.unpack('<QQ', raw[12:28])
    if not catalog or not user or not catalog ^ user:
        raise RuntimeError('Existing Music identity is incomplete')
    return f'{catalog ^ user:016X}'


def read_catalogue(path):
    with sqlite3.connect(path) as db:
        db.row_factory = sqlite3.Row
        schema = db.execute('pragma user_version').fetchone()[0]
        if schema not in (2400060, 2410000):
            raise RuntimeError(f'Untested Music database schema {schema}')
        if db.execute('pragma integrity_check').fetchone()[0] != 'ok':
            raise RuntimeError('Music catalogue integrity check failed')
        info = dict(db.execute('select db_pid,primary_container_pid from db_info').fetchone())
        if not all(info.values()):
            raise RuntimeError('Music database identity is incomplete')
        anchor = db.execute("select value from _MLDatabaseProperties where key='MLSyncClientSyncAnchor'").fetchone()
        synced = db.execute('select count(*) from item_store where sync_id<>0').fetchone()[0]
        local = db.execute("select count(*) from item_extra where location<>'' and file_size>0").fetchone()[0]
        if anchor is None:
            if synced or local:
                raise RuntimeError('Missing sync anchor on a populated local library')
            anchor = 0  # Never-synced cloud-only library; Ready must confirm zero.
        else:
            anchor = int(anchor[0])
        if not 0 <= anchor < 2**32 - 1:
            raise RuntimeError('Invalid Music sync revision')
        tracks = [dict(r) for r in db.execute('''select i.item_pid,s.sync_id,e.title,e.location,
            e.file_size,e.total_time_ms,coalesce(b.path,'') base_path
            from item i join item_extra e using(item_pid) join item_store s using(item_pid)
            left join base_location b using(base_location_id)''')]
        occupied = set()
        for table in ('item_store', 'album', 'album_artist', 'composer', 'container'):
            occupied.update(r[0] for r in db.execute('select sync_id from '+table))
        groups=[dict(r) for r in db.execute('''select a.album,a.sync_id,
            b.album_artist,b.sync_id artist_sync_id from album a
            left join album_artist b using(album_artist_pid) where a.sync_id<>0''')]
        artwork_ids={int(r[0]) for r in db.execute("select artwork_token from artwork where artwork_source_type=300")}
        artwork_ids.update(int(r[0]) for r in db.execute("select artwork_token from artwork_token where artwork_source_type=300"))
        return {**info, 'anchor':anchor, 'schema':schema, 'tracks':tracks, 'groups':groups,
                'artwork_ids':artwork_ids,
                'occupied':occupied, 'synced':synced, 'local':local}


def native_format(track):
    """Wire profiles from rustunes media/format and protocol/mpeg."""
    if track.format == 'ALAC' and track.channels<=2 and track.rate<=192000 and track.bits<=24:
        return 601, 'Apple Lossless audio file', '.m4a', None
    if track.rate>48000 or track.channels>2:
        return None
    audio=mutagen.File(track.path)
    if isinstance(audio,MP3) and audio.info.layer==3:
        code={1:301,2:302,2.5:303}.get(audio.info.version)
        if code:return code,'MPEG audio file','.mp3',audio.info
    if isinstance(audio,MP4):
        code={'mp4a.40.2':502,'mp4a.40.5':508,'mp4a.40.29':509}.get(str(audio.info.codec).lower())
        if code:return code,'AAC audio file','.m4a',audio.info
    return None


def jpeg_artwork(track):
    if track.cover and not track.cover.startswith(b'\xff\xd8'):
        import io
        from PIL import Image
        image=Image.open(io.BytesIO(track.cover)).convert('RGB');image.thumbnail((1200,1200))
        output=io.BytesIO();image.save(output,format='JPEG',quality=94)
        track.cover=output.getvalue()
    return track


def prepare_audio(source, cache, stop=None, progress=lambda value:None):
    track=inspect_track(Path(source))
    if native_format(track):
        return jpeg_artwork(track)
    return jpeg_artwork(prepare_alac(source,cache,stop,progress))


def prepare_alac(source, cache, stop=None, progress=lambda value:None):
    """Initial modern profile: ALAC, preserving source rate/depth up to 192/24."""
    track=inspect_track(Path(source))
    cache=Path(cache);cache.mkdir(parents=True,exist_ok=True)
    if track.format=='ALAC' and track.channels<=2 and track.rate<=192000 and track.bits<=24:
        local=track.path
    else:
        if track.path.stat().st_size>1024**3:
            raise RuntimeError('This iPhone preview supports source files up to 1 GB.')
        with track.path.open('rb') as source_file:
            digest=hashlib.file_digest(source_file,'sha256').hexdigest()
        local=cache/(digest+'.m4a')
        if local.exists():
            cached=inspect_track(local)
            if cached.format=='ALAC' and abs(cached.duration-track.duration)<1:
                return cached
        temporary=local.with_suffix('.part.m4a')
        cmd=[ffmpeg(),'-nostdin','-y','-v','error','-i',str(track.path),'-map','0:a:0',
             '-vn','-map_metadata','0','-c:a','alac','-sample_fmt','s16p' if track.bits<=16 else 's32p']
        if track.channels>2:cmd+=['-ac','2']
        if track.rate>192000:cmd+=['-ar','192000']
        cmd+=['-progress','pipe:1','-nostats',str(temporary)]
        import tempfile
        with tempfile.TemporaryFile() as error_log:
            process=subprocess.Popen(cmd,stdout=subprocess.PIPE,stderr=error_log,text=True,
                encoding='utf-8',errors='replace',creationflags=subprocess.CREATE_NO_WINDOW)
            try:
                for line in process.stdout:
                    if stop is not None and stop.is_set():
                        raise Cancelled()
                    if line.startswith('out_time_us='):
                        try:progress(min(.99,int(line.split('=',1)[1])/1e6/track.duration))
                        except ValueError:pass
                if process.wait():
                    error_log.seek(0)
                    raise RuntimeError(error_log.read().decode('utf-8',errors='replace')[-500:])
            except BaseException:
                if process.poll() is None:
                    process.terminate();process.wait(timeout=10)
                temporary.unlink(missing_ok=True)
                raise
            finally:
                process.stdout.close()
        audio=MP4(temporary)
        for key,value in [('©nam',track.title),('©ART',track.artist),('©alb',track.album),
                          ('aART',track.album_artist),('©gen',track.genre),('cprt',track.copyright)]:
            if value:audio[key]=[value]
        audio['trkn']=[(track.number,track.total)]
        if track.year:audio['©day']=[str(track.year)]
        if track.cover:
            import io
            from PIL import Image
            image=Image.open(io.BytesIO(track.cover)).convert('RGB');image.thumbnail((1200,1200))
            output=io.BytesIO();image.save(output,format='JPEG',quality=94)
            audio['covr']=[MP4Cover(output.getvalue(),imageformat=MP4Cover.FORMAT_JPEG)]
        audio.save()
        temporary.replace(local)
    result=inspect_track(local)
    if result.format!='ALAC':raise RuntimeError('ALAC preparation failed')
    return result


def insert_operation(catalog, track, cleared=()):
    def allocate():
        while True:
            pid=secrets.randbits(63)
            if pid>=1000 and pid not in used:
                used.add(pid);return pid
    used=set(catalog['occupied'])|{catalog['db_pid'],catalog['primary_container_pid']}
    pid=allocate()
    artist=track.album_artist or track.artist
    group=next((g for g in catalog['groups'] if g['album']==track.album and g['album_artist']==artist),None)
    album_pid=group['sync_id'] if group else allocate()
    artist_pid=group['artist_sync_id'] if group else next((g['artist_sync_id'] for g in catalog['groups'] if g['album_artist']==artist),None)
    artist_pid=artist_pid or allocate()
    samples=round(track.duration*track.rate)
    item={'title':track.title,'artist':track.artist,'album':track.album,'album_artist':artist,
        'sort_name':track.title,'sort_artist':track.artist,'sort_album':track.album,'sort_album_artist':artist,
        'album_pid':album_pid,'artist_pid':artist_pid,'total_time_ms':samples*1000//track.rate,
        'track_number':track.number,'disc_number':1,'is_song':True,'remember_bookmark':False,'artwork_checksum':0}
    for k,v in [('genre',track.genre),('copyright',track.copyright),('year',track.year),('track_count',track.total)]:
        if v:item[k]=v
    cover=track.cover
    if cover:
        if not cover.startswith(b'\xff\xd8'):
            raise RuntimeError('Modern artwork must be JPEG')
        cache_id=max(catalog['artwork_ids']|{99})+1
        if cache_id>=2**32:raise RuntimeError('Artwork identifiers exhausted')
        item.update(artwork_checksum=len(cover)+17,artwork_cache_id=cache_id)
    database={'operation':'update_db_info','pid':catalog['db_pid'],
              'db_info':{'primary_container_pid':catalog['primary_container_pid']}}
    insert={'operation':'insert_track','pid':pid,'item':item,
        'avformat_info':{'audio_format':601,'sample_rate':float(track.rate),
            'bit_rate':track.rate*track.bits*track.channels//1000,'duration':samples,
            'gapless_encoding_delay':0,'gapless_encoding_drain':0,'gapless_heuristic_info':1,
            'gapless_last_frame_resynch':0,'analysis_inhibit_flags':2},
        'item_stats':{'play_count_user':0,'play_count_recent':0,'skip_count_user':0,
                      'skip_count_recent':0,'has_been_played':False},
        'location':{'kind':'Apple Lossless audio file'}}
    if getattr(track,'format','ALAC')!='ALAC':
        profile=native_format(track)
        if not profile:raise RuntimeError('Audio was not prepared for native Music import')
        code,kind,_,audio=profile
        # Native MediaLibrary probes compressed frame timing itself. This is
        # rustunes' Timing::Omitted profile; don't invent MP3 encoder delay.
        item.pop('total_time_ms',None)
        insert['avformat_info']={'audio_format':code,'sample_rate':float(track.rate),
            'bit_rate':round(audio.bitrate/1000),'channels':track.channels}
        insert['location']['kind']=kind
    envelope={'revision':catalog['anchor']+1,'timestamp':datetime.now(timezone.utc).replace(tzinfo=None),
              'operations':[database,*({'operation':'delete_track','pid':r['pid']} for r in cleared),insert]}
    return pid,plistlib.dumps(envelope,fmt=plistlib.FMT_BINARY,sort_keys=False),cover


class ModernDevice:
    def __init__(self, udid, backup_root, emit=lambda e: None, host=None, pair_record=None):
        self.udid, self.root, self.emit = udid, Path(backup_root), emit
        self.host, self.pair_record = host, pair_record
        self.stack = AsyncExitStack()
        self.atc = None
        self.catalogue = None
        self.reply = None
        self.frames = []
        self.send_lock = asyncio.Lock()
        self.reader = None
        self.reader_error = None
        self.inbox = None
        self.completion_sent = False

    async def __aenter__(self):
        try:
            if self.host:
                connection = await connect_paired_wifi(self.host,self.udid,self.pair_record)
            else:
                connection = await create_using_usbmux(serial=self.udid,connection_type='USB',
                    autopair=False,label='iPods Music')
            self.lockdown = await self.stack.enter_async_context(connection)
            values = self.lockdown.all_values
            if values.get('UniqueDeviceID') != self.udid:
                raise RuntimeError('Connected device identity differs')
            self.info = {k:values[k] for k in ['ProductType','ProductVersion','BuildVersion']}
            if not self.info['ProductVersion'].startswith('27.'):
                raise RuntimeError('This experimental backend requires iOS 27')
            # Establish heartbeat BEFORE any Wi-Fi file/ATC service. Starting
            # two services concurrently also races Lockdown request/reply pairs.
            self.heartbeat_link = await self.stack.enter_async_context(
                await self.lockdown.start_lockdown_service('com.apple.mobile.heartbeat'))
            self.heartbeat = asyncio.create_task(self.keep_alive())
            self.afc = await self.stack.enter_async_context(AfcService(self.lockdown))
            return self
        except BaseException:
            await self.__aexit__(None,None,None)
            raise

    async def keep_alive(self):
        while True:
            await self.heartbeat_link.recv_plist()
            await self.heartbeat_link.send_plist({'Command':'Polo'})

    async def __aexit__(self,*args):
        await self.stop_reader()
        if hasattr(self,'heartbeat'):
            self.heartbeat.cancel()
            with suppress(asyncio.CancelledError):
                await self.heartbeat
        await self.stack.aclose()

    async def exists(self,path):
        try:
            await self.afc.stat(path)
            return True
        except AfcFileNotFoundError:
            return False

    async def read_file(self,path,limit=256*1024**2):
        before = await self.afc.stat(path)
        size = int(before['st_size'])
        if before['st_ifmt'] != 'S_IFREG' or size > limit:
            raise RuntimeError('Unexpected device file type or size')
        handle = await self.afc.fopen(path,'r')
        try:
            data = await self.afc.fread(handle,size)
        finally:
            await self.afc.fclose(handle)
        if len(data)!=size or await self.afc.stat(path)!=before:
            raise RuntimeError('Device file changed during read')
        return data

    async def snapshot(self,label):
        directory = self.root / (datetime.now().strftime('%Y-%m-%d_%H-%M-%S')+'-'+label+'-'+secrets.token_hex(3))
        directory.mkdir(parents=True)
        names=['MediaLibrary.sqlitedb','MediaLibrary.sqlitedb-wal','MediaLibrary.sqlitedb-shm','iTunesPrefs.plist']
        for attempt in range(3):
            existing=[n for n in names if await self.exists(MUSIC_DIR+'/'+n)]
            before={n:await self.afc.stat(MUSIC_DIR+'/'+n) for n in existing}
            for name in existing:
                (directory/name).write_bytes(await self.read_file(MUSIC_DIR+'/'+name))
            after={n:await self.afc.stat(MUSIC_DIR+'/'+n) for n in existing}
            if before==after:
                break
        else:
            raise RuntimeError('Music catalogue kept changing during capture')
        self.identity = library_identity((directory/'iTunesPrefs.plist').read_bytes())
        self.catalogue = read_catalogue(directory/'MediaLibrary.sqlitedb')
        self.backup = directory
        return self.catalogue

    async def staging_empty(self):
        if await self.exists(STAGING):
            names=[n for n in await self.afc.listdir(STAGING) if n not in ('.','..')]
            if names:
                raise RuntimeError('Another Music sync has files in the staging directory')

    async def send(self,command,params=None,session=2):
        message={'Command':command,'Session':session}
        if params is not None:
            message['Params']=params
        async with self.send_lock:
            await self.atc.send_plist(message,endianity='<',fmt=plistlib.FMT_BINARY)
        self.frames.append({'direction':'send','command':command,'session':session})

    async def read_frame(self,timeout=30):
        # A cancelled or partial read invalidates this connection; callers do
        # not attempt to continue reading it after any exception.
        data=await asyncio.wait_for(self.atc.recvall(4),timeout)
        size=struct.unpack('<I',data)[0]
        if not 0<size<=MAX_FRAME:
            raise RuntimeError('Invalid ATC frame size')
        raw=await asyncio.wait_for(self.atc.recvall(size),timeout)
        message=plistlib.loads(raw)
        if not isinstance(message,dict) or not isinstance(message.get('Command'),str):
            raise RuntimeError('Invalid ATC message')
        command=message['Command']
        self.frames.append({'direction':'receive','command':command,'session':message.get('Session')})
        if command in ('SyncFailed','SyncCanceled'):
            raise RuntimeError('iPhone rejected Music sync: '+str(message.get('Params',{}).get('ErrorCode','unknown')))
        return message

    async def answer_ping(self,message):
        if 'Params' in message or not isinstance(message.get('Session'),int):
            raise RuntimeError('Invalid Music keepalive request')
        await self.send('Pong',session=message['Session'])

    async def receive(self,timeout=30):
        self.check_atc()
        if self.inbox is not None:
            result=await asyncio.wait_for(self.inbox.get(),timeout)
            if isinstance(result,BaseException):raise result
            return result
        deadline=time.monotonic()+timeout
        for _ in range(64):
            message=await self.read_frame(max(.01,deadline-time.monotonic()))
            if message['Command']!='Ping':return message
            await self.answer_ping(message)
        raise RuntimeError('Music keepalive frame limit exceeded')

    def check_atc(self):
        if self.reader_error is not None:raise self.reader_error

    async def pump_atc(self):
        try:
            while True:
                message=await self.read_frame(timeout=120)
                if message['Command']=='Ping':
                    await self.answer_ping(message)
                    continue
                if message['Command']=='SyncFinished' and not self.completion_sent:
                    raise RuntimeError('Music finished before the requested audio was completed')
                self.inbox.put_nowait(message)
                if message['Command']=='SyncFinished':return
        except asyncio.CancelledError:
            raise
        except Exception as error:
            self.reader_error=error
            with suppress(asyncio.QueueFull):self.inbox.put_nowait(error)

    def start_reader(self):
        self.inbox=asyncio.Queue(maxsize=256)
        self.reader=asyncio.create_task(self.pump_atc())

    async def stop_reader(self):
        if self.reader is not None:
            self.reader.cancel()
            with suppress(asyncio.CancelledError):await self.reader
            self.reader=None

    async def ready(self):
        if self.catalogue is None:
            raise RuntimeError('Read and preserve the current catalogue first')
        # Standard Apple sync lock. Do not overlap another Apple/client writer.
        handle=await self.afc.fopen('/com.apple.itunes.lock_sync','r+')
        self.stack.push_async_callback(self.afc.fclose,handle)
        await asyncio.wait_for(self.afc.lock(handle,6),5)
        await self.staging_empty()
        self.atc=await self.stack.enter_async_context(await self.lockdown.start_lockdown_service('com.apple.atc'))
        await self.send('Ping',session=1)
        seen=set()
        for _ in range(64):
            message=await self.receive()
            cmd,p=message['Command'],message.get('Params',{})
            if cmd=='Capabilities':
                if p.get('GrappaSupportInfo')!={'version':1,'deviceType':0,'protocolVersion':1}:
                    raise RuntimeError('Unsupported Music signing capabilities')
            elif cmd=='SyncAllowed':
                if (p.get('ManualSync') is not True or p.get('DataProtected') is not False
                        or p.get('EducationModeEnabled') is not False or 'Music' in p.get('DisabledAssetTypes',[])):
                    raise RuntimeError('iPhone does not allow this Music sync session')
            elif cmd=='ReadyForSync':
                if 'RequestingSync' not in seen or message.get('Session')!=2:
                    raise RuntimeError('Unexpected Music session response')
                if p.get('DataclassAnchors',{}).get('Media')!=str(self.catalogue['anchor']):
                    raise RuntimeError('Music revision changed during handshake')
                self.reply=p.get('DeviceInfo',{}).get('Grappa')
                if not isinstance(self.reply,bytes) or len(self.reply)!=83:
                    raise RuntimeError('Unsupported Music signing reply')
                await self.staging_empty()
                self.start_reader()
                return
            elif cmd not in ('Pong','InstalledAssets','AssetMetrics','Progress'):
                raise RuntimeError('Unexpected Music handshake command: '+cmd)
            seen.add(cmd)
            if {'Pong','Capabilities','SyncAllowed'}<=seen and 'RequestingSync' not in seen:
                await self.send('RequestingSync',{
                    'Dataclasses':['Media','Book','Keybag'],
                    'DataclassAnchors':{'Media':str(self.catalogue['anchor']),'VoiceMemo':'1'},
                    'HostInfo':{'Type':'iTunes','Version':'13.2.4.123','WindowsOSVersion':'10.0',
                        'SyncHostName':'iPods Are Beautiful','LibraryID':self.identity,
                        'SyncedDataclasses':['Media'],'SyncedAssetTypes':['Music'],'Wakeable':0,
                        'Grappa':native_sign('request')}})
                seen.add('RequestingSync')
        raise RuntimeError('Music handshake exceeded its frame limit')

    async def write_new(self,path,data):
        if await self.exists(path):
            raise RuntimeError('Refusing to overwrite an existing device file')
        await self.afc.set_file_contents(path,data)
        actual=await self.read_file(path,limit=max(len(data),1))
        if actual!=data:raise RuntimeError('Device file readback differs')

    async def upload_audio(self,local,asset_id,extension='.m4a'):
        source=Path(local);size=source.stat().st_size
        if size>512*1024**2:raise RuntimeError('Experimental audio size bound exceeded')
        await self.afc.makedirs('/Airlock/Media')
        partial='/Airlock/Media/'+asset_id+extension;staged='/Airlock/Media/'+asset_id
        if await self.exists(partial) or await self.exists(staged):raise RuntimeError('Asset staging collision')
        handle=await self.afc.fopen(partial,'w')
        digest=hashlib.sha256();done=0
        try:
            with source.open('rb') as f:
                while chunk:=f.read(1024*1024):
                    self.check_atc()
                    await self.afc.fwrite(handle,chunk)
                    digest.update(chunk);done+=len(chunk)
                    self.emit({'stage':'Uploading','bytes_transferred':done,'bytes_total':size})
        finally:
            await self.afc.fclose(handle)
        if source.stat().st_size!=size or done!=size:raise RuntimeError('Source changed during upload')
        remote=await self.read_file(partial,limit=size)
        if hashlib.sha256(remote).digest()!=digest.digest():raise RuntimeError('Uploaded audio checksum differs')
        await self.afc.rename(partial,staged)
        return digest.hexdigest(),size

    async def push_alac(self,track):
        if track.path.stat().st_size>512*1024**2:
            raise RuntimeError('This iPhone preview supports prepared tracks up to 512 MB.')
        profile=native_format(track)
        if not profile:raise RuntimeError('Unsupported prepared audio profile')
        extension=profile[2]
        before=self.catalogue
        cleared=interrupted_imports(before,[self.root,DATA/'modern-backups'])
        pid,operation,cover=insert_operation(before,track,cleared)
        asset_id=str(pid)
        journal=self.backup/'operation.plist';journal.write_bytes(operation)
        # Journal precedes all device mutations. A failed run is never blindly retried.
        report={'pid':str(pid),'device':self.udid,'source':str(track.path),'state':'prepared','frames':self.frames,
                'clearedIncomplete':cleared}
        report_file=self.backup/'transfer.json'
        def save():report_file.write_text(json.dumps(report,indent=2),encoding='utf-8')
        save()
        await self.ready()
        signature=native_sign('sign',struct.pack('<I',len(operation))+self.reply+operation)
        if len(signature)!=21:raise RuntimeError('Unexpected signature size')
        await self.afc.makedirs(STAGING)
        await self.staging_empty()
        report['state']='staging';save()
        await self.write_new(OPERATION,operation)
        await self.write_new(SIGNATURE,signature)
        await self.send('FinishedSyncingMetadata',{
            'DataclassAnchors':{'Media':str(before['anchor']+1),'VoiceMemo':'1'},
            'SyncTypes':{'Media':1,'Book':0,'Keybag':1}})
        report['state']='metadata-sent';save()
        manifest=None
        for _ in range(128):
            message=await self.receive()
            if message.get('Session')!=2:raise RuntimeError('Mismatched transfer session')
            if message['Command']=='AssetManifest':
                manifest=message.get('Params',{}).get('AssetManifest',{}).get('Media');break
            if message['Command'] not in ('Progress','SyncAllowed','InstalledAssets','AssetMetrics'):
                raise RuntimeError('Unexpected response before assets: '+message['Command'])
        if not isinstance(manifest,list) or not 0<len(manifest)<=1024:raise RuntimeError('Invalid Music manifest')
        (self.backup/'asset-manifest.plist').write_bytes(plistlib.dumps(manifest,fmt=plistlib.FMT_BINARY))
        report['manifest']=manifest;save()
        owned=[];ancillary=[]
        for asset in manifest:
            if asset.get('AssetType')!='Music' or asset.get('IsDownload') is not True:
                raise RuntimeError('Unsupported requested asset type or direction')
            aid=asset.get('AssetID');parts=asset.get('Variant',{}).get('AssetParts')
            if aid==asset_id:
                if parts not in ((3,7) if cover else (1,5)):raise RuntimeError('Unexpected own asset parts')
                owned.append(asset)
            elif (isinstance(aid,str) and aid.lstrip('-').isdigit() and
                  (int(aid)&(2**64-1)) in {p&(2**64-1) for p in before['occupied']}
                  and int(aid)!=0 and parts in (2,4)):
                ancillary.append(aid)
            else:
                previous=next((r for r in before['tracks'] if isinstance(aid,str) and aid.lstrip('-').isdigit()
                               and (r['sync_id']&(2**64-1))==(int(aid)&(2**64-1))),None)
                name=previous['title'] if previous else str(aid)
                raise RuntimeError(f'Music requested an unsupported existing asset: {name} (parts {parts}).')
        if len(owned)!=1:raise RuntimeError('Music manifest must request exactly our new track')
        report['state']='uploading';save()
        if cover:
            await self.afc.makedirs('/Airlock/Media/Artwork')
            await self.write_new('/Airlock/Media/Artwork/'+asset_id,cover)
        digest,size=await self.upload_audio(track.path,asset_id,extension)
        # New permanent names are checked against both device and current catalogue.
        while True:
            folder=f'iTunes_Control/Music/F{secrets.randbelow(50):02}'
            final=folder+'/'+''.join(secrets.choice('ABCDEFGHIJKLMNOPQRSTUVWXYZ') for _ in range(8))+extension
            if not await self.exists('/'+final):break
        await self.afc.makedirs('/'+folder)
        self.check_atc()
        self.completion_sent=True
        await self.send('FileComplete',{'AssetID':asset_id,'Dataclass':'Media','AssetPath':final})
        for aid in ancillary:
            await self.send('FileError',{'AssetID':aid,'Dataclass':'Media','ErrorCode':16,'ErrorIsVisible':False})
        for _ in range(256):
            message=await self.receive(timeout=60)
            if message.get('Session')!=2:raise RuntimeError('Mismatched terminal session')
            if message['Command']=='SyncFinished':break
            if message['Command'] not in ('Progress','SyncAllowed','InstalledAssets','AssetMetrics'):
                raise RuntimeError('Unexpected response after assets: '+message['Command'])
        else:raise RuntimeError('Music sync did not finish')
        report.update(state='native-finished',sha256=digest,bytes=size,asset=final);save()
        await self.stop_reader()
        await self.atc.close();self.atc=None
        self.emit({'stage':'Verifying library','bytes_transferred':size,'bytes_total':size})
        await asyncio.sleep(.5)
        after=await self.snapshot('after')
        by_id={r['sync_id']:r for r in after['tracks'] if r['sync_id']}
        ready=by_id.get(pid)
        if not ready or ready['file_size']!=size or not ready['location']:
            raise RuntimeError('iPhone did not install the complete new track')
        path='/'+(ready['base_path'].strip('/')+'/'+ready['location'].lstrip('/')).strip('/')
        if hashlib.sha256(await self.read_file(path,limit=size)).hexdigest()!=digest:
            raise RuntimeError('Installed track checksum differs')
        removed={r['item_pid'] for r in cleared}
        old={r['item_pid']:r for r in before['tracks'] if r['item_pid'] not in removed}
        new={r['item_pid']:r for r in after['tracks']}
        if any(new.get(k)!=v for k,v in old.items()) or len(new)!=len(old)+1:
            raise RuntimeError('Existing Music entries changed unexpectedly')
        if after['anchor']!=before['anchor']+1:raise RuntimeError('Music sync revision was not committed')
        report.update(state='verified',oldTracksPreserved=len(old));save()
        return report

    async def already_present(self,track):
        expected=hashlib.sha256(track.path.read_bytes()).hexdigest()
        for r in self.catalogue['tracks']:
            if (r['sync_id'] and r['title']==track.title and r['file_size']==track.path.stat().st_size
                    and r['location']):
                path='/'+(r['base_path'].strip('/')+'/'+r['location'].lstrip('/')).strip('/')
                if not path.startswith('/iTunes_Control/Music/'):
                    continue
                actual=await self.read_file(path,limit=r['file_size'])
                if hashlib.sha256(actual).hexdigest()==expected:
                    return {'state':'already-present','pid':str(r['sync_id']),'bytes':0,'skipped':True}
        recoverable={r['pid'] for r in interrupted_imports(self.catalogue,[self.root,DATA/'modern-backups'])}
        if any(r['sync_id'] and r['title']==track.title and r['sync_id'] not in recoverable and
               (not r['file_size'] or not r['location']) for r in self.catalogue['tracks']):
            raise RuntimeError('An unfinished import of this track is still in Music. No duplicate was added.')
        return None


async def probe(udid,root):
    async with ModernDevice(udid,root) as d:
        c=await d.snapshot('before')
        report={**d.info,'schema':c['schema'],'tracks':len(c['tracks']),
                'local':c['local'],'anchor':c['anchor'],'backup':str(d.backup)}
        try:
            await d.ready()
            report['ready']=True
        finally:
            report['frames']=d.frames
            (d.backup/'probe.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
            print(json.dumps(report),flush=True)
        return report


async def push(udid,source,root,emit=lambda e:None,host=None,pair_record=None):
    track=await asyncio.to_thread(prepare_audio,source,Path(root)/'conversion-cache')
    async with ModernDevice(udid,root,emit,host=host,pair_record=pair_record) as device:
        await asyncio.wait_for(device.snapshot('before'),60)
        known=await device.already_present(track)
        if known:return known
        return await asyncio.wait_for(device.push_alac(track),360)


if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('command',choices=['probe','push']);p.add_argument('--udid',required=True)
    p.add_argument('--backups',default=str(DATA/'modern-backups'))
    p.add_argument('--file',type=Path)
    args=p.parse_args()
    if args.command=='probe':asyncio.run(probe(args.udid,args.backups))
    else:
        if not args.file:p.error('--file is required')
        result=asyncio.run(push(args.udid,args.file,args.backups))
        print(json.dumps({k:v for k,v in result.items() if k!='frames'}),flush=True)
