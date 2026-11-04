from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from modern_engine import ModernEngine
from transfer_speed import TransferSpeed


class Device:
    imports = 0
    fail = False
    stop = None
    known = False
    async def __aenter__(self):return self
    async def __aexit__(self,*args):pass
    def __init__(self,udid,root,emit,**kwargs):
        self.emit=emit
        self.afc=self
    async def snapshot(self,*args):return {}
    async def already_present(self,track):return {'skipped':True} if self.known else None
    async def get_device_info(self):return {'FSFreeBytes':10**12,'FSTotalBytes':2*10**12}
    async def push_alac(self,track):
        if self.fail:raise ConnectionError('Lost connection after metadata')
        self.emit({'stage':'Uploading','bytes_transferred':track.path.stat().st_size})
        if self.stop:self.stop.set()
        self.imports+=1
        return {'state':'verified'}


class ModernQueueTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.a=self.root/'a.flac';self.a.write_bytes(b'a'*10)
        self.b=self.root/'b.flac';self.b.write_bytes(b'b'*20)
        self.events=[]
        Device.imports=0;Device.fail=False;Device.stop=None;Device.known=False
        self.patches=[patch('modern_engine.ModernDevice',Device),
            patch('modern_engine.DATA',self.root),
            patch('modern_engine.remember_capacity'),
            patch('modern_engine.inspect',lambda p:SimpleNamespace(title=p.stem,format='FLAC')),
            patch('modern_engine.native_format',return_value=None),
            patch('modern_engine.prepare_audio',lambda p,*a:SimpleNamespace(path=p,format='ALAC'))]
        for p in self.patches:p.start()
        self.config={'udid':'test-only','backup_root':str(self.root),'host':None}
    def tearDown(self):
        for p in reversed(self.patches):p.stop()
        self.tmp.cleanup()
    def test_counts_actual_confirmed_imports_and_payload_bytes(self):
        r=ModernEngine(self.config,self.events.append).transfer([self.a,self.a,self.b])
        self.assertEqual((r['tracks_total'],r['imported'],r['bytes_transferred'],r['bytes_total']),(2,2,30,30))
        self.assertFalse(r['bytes_estimated'])
        self.assertEqual(len([e for e in self.events if e.get('state')=='On iPhone']),2)
    def test_skip_is_not_reported_as_uploaded_or_imported(self):
        Device.known=True
        r=ModernEngine(self.config,self.events.append).transfer([self.a])
        self.assertEqual((r['skipped'],r['imported'],r['bytes_transferred'],r['bytes_total']),(1,0,0,0))
    def test_eject_finishes_current_track_and_stops_next(self):
        stop=threading.Event();Device.stop=stop
        r=ModernEngine(self.config,self.events.append,stop).transfer([self.a,self.b])
        self.assertTrue(r['cancelled'])
        self.assertEqual((r['imported'],r['bytes_transferred']),(1,10))
    def test_ambiguous_failure_never_starts_another_session(self):
        Device.fail=True
        with patch.object(Device,'push_alac',side_effect=ConnectionError('metadata sent')) as upload:
            r=ModernEngine(self.config,self.events.append).transfer([self.a,self.b])
        self.assertEqual(upload.call_count,1)
        self.assertTrue(r['interrupted'])
        self.assertEqual((r['imported'],r['errors']),(0,1))


class SpeedTests(unittest.TestCase):
    def test_speed_uses_payload_bytes_and_resets_between_stages(self):
        now=[10.0];meter=TransferSpeed(lambda:now[0])
        meter.update(0,False)
        now[0]=11
        self.assertEqual(meter.update(2*1024**2),2*1024**2)
        now[0]=12
        self.assertEqual(meter.update(4*1024**2),2*1024**2)
        now[0]=13
        self.assertEqual(meter.update(4*1024**2,False),0)
        now[0]=14
        self.assertEqual(meter.update(5*1024**2),1024**2)


if __name__=='__main__':unittest.main()
