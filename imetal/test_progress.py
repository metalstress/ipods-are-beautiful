from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import hashlib
import tempfile
import threading
import unittest

from engine import Engine,Bridge
from media import Track


class FakeSession:
    instances=[]
    def __init__(self,config):
        self.config=config
        self.mode='USB'
        self.sftp=self
        self.size=0
        self.digest=''
        self.put_calls=0
        self.client=SimpleNamespace(get_transport=lambda:SimpleNamespace(is_active=lambda:True))
        self.instances.append(self)
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def info(self):return {'mode':'USB','free':10**12,'total':2*10**12,'name':'iPod touch 4'}
    def put(self,path,remote,callback=None):
        data=Path(path).read_bytes()
        self.size=len(data);self.digest=hashlib.sha256(data).hexdigest();self.put_calls+=1
        if callback:
            if self.config.get('cancel'):
                self.config['cancel'].set()
            callback(len(data)//2,len(data))
            callback(len(data),len(data))
    def chmod(self,*args):pass
    def posix_rename(self,*args):pass
    def remove(self,*args):pass
    def stat(self,path):return SimpleNamespace(st_size=self.size)
    def run(self,command,*args,**kwargs):
        return self.digest if command.startswith('openssl ') else ''


class FakeBridge:
    match=staticmethod(Bridge.match)
    def __init__(self,session):self.records=[]
    def ensure(self):pass
    def library(self,title=None):return self.records
    def entries(self,title=None):return self.library(title)
    def finish_imports(self,imports,last_add):pass
    def add(self,track,*args):
        self.records.append({'pid':'1','title':track.title,'artist':track.artist,'album':track.album,
            'duration':track.duration,'asset':'/var/mobile/Media/iTunes_Control/Music/F00/test.mp3','size':track.path.stat().st_size})


class ProgressTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.a=self.root/'a.mp3';self.a.write_bytes(b'a'*10)
        self.b=self.root/'b.mp3';self.b.write_bytes(b'b'*20)
        self.events=[]
        self.patches=[patch('engine.Session',FakeSession),patch('engine.Bridge',FakeBridge),
            patch('engine.DATA',self.root),patch('engine.inspect',lambda p:Track(p,p.stem,'Artist','Album',1,True,'MP3')),
            patch('engine.fingerprint',lambda t,s:setattr(t,'digest',t.path.stem)),
            patch('engine.convert',lambda t,s,p:t.path),patch('engine.time.sleep'),patch.object(Engine,'history')]
        for p in self.patches:p.start()
    def tearDown(self):
        for p in reversed(self.patches):p.stop()
        self.tmp.cleanup()

    def test_actual_upload_bytes_and_confirmed_import_count(self):
        result=Engine({},self.events.append).transfer([self.a,self.b])
        self.assertEqual((result['imported'],result['tracks_total']),(2,2))
        self.assertEqual((result['bytes_transferred'],result['bytes_total']),(30,30))
        self.assertFalse(result['bytes_estimated'])
        upload=[e for e in self.events if e.get('state')=='Загрузка']
        self.assertTrue(any(e['bytes_transferred']==20 and e['imported']==1 for e in upload))
        self.assertTrue(all(e['imported']<=e['tracks_total'] for e in upload))

    def test_cancel_keeps_partial_byte_count_without_claiming_import(self):
        stop=threading.Event()
        result=Engine({'cancel':stop},self.events.append,stop).transfer([self.a])
        self.assertTrue(result['cancelled'])
        self.assertEqual(result['imported'],0)
        self.assertEqual(result['bytes_transferred'],5)

    def test_existing_track_is_not_counted_as_uploaded(self):
        known={'pid':'2','title':'a','artist':'Artist','album':'Album','duration':1,
               'asset':'/var/mobile/Media/iTunes_Control/Music/F00/known.mp3','size':10}
        with patch.object(FakeBridge,'library',return_value=[known]):
            result=Engine({},self.events.append).transfer([self.a])
        self.assertEqual((result['imported'],result['skipped']),(0,1))
        self.assertEqual((result['bytes_transferred'],result['bytes_total']),(0,0))
        self.assertEqual(FakeSession.instances[-1].put_calls,0)

    def test_pending_only_song_is_never_reenqueued(self):
        pending={'pid':'2','title':'a','artist':'Artist','album':'Album','duration':1,
                 'asset':'','size':0}
        with patch.object(FakeBridge,'entries',return_value=[pending]), patch.object(FakeBridge,'add') as add:
            result=Engine({},self.events.append).transfer([self.a])
        self.assertEqual((result['imported'],result['errors']),(0,1))
        add.assert_not_called()
        self.assertEqual(FakeSession.instances[-1].put_calls,0)

    def test_duplicate_paths_are_transferred_once(self):
        result=Engine({},self.events.append).transfer([self.a,self.a])
        self.assertEqual((result['imported'],result['tracks_total']),(1,1))
        self.assertEqual(FakeSession.instances[-1].put_calls,1)


if __name__=='__main__':unittest.main()
