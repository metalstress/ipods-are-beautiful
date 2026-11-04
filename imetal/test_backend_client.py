import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from backend_client import run_backend


class BackendClientTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.script=self.root/'worker.py'
        self.script.write_text('''import json,sys,time
from pathlib import Path
p=Path(sys.argv[1]);r=json.loads((p/'request.json').read_text(encoding='utf-8'))
if r['config'].get('crash'):sys.exit(7)
with (p/'events.jsonl').open('w',encoding='utf-8',buffering=1) as f:
    line=json.dumps({'title':'Песня','stage':'Uploading'},ensure_ascii=False)+'\\n'
    f.write(line[:12]);f.flush();time.sleep(.1);f.write(line[12:]);f.flush()
if r['config'].get('wait_stop'):
    deadline=time.monotonic()+3
    while not (p/'stop').exists() and time.monotonic()<deadline:time.sleep(.02)
result={'imported':1,'cancelled':(p/'stop').exists()}
(p/'result.json').write_text(json.dumps(result),encoding='utf-8')
''',encoding='utf-8')
        self.patches=[patch('backend_client.DATA',self.root),
                      patch('backend_client.worker_command',lambda d:[sys.executable,str(self.script),str(d)])]
        for p in self.patches:p.start()
    def tearDown(self):
        for p in reversed(self.patches):p.stop()
        self.tmp.cleanup()
    def test_partial_unicode_progress_is_delivered_once_and_result_survives(self):
        events=[]
        r=run_backend('transfer',{},emit=events.append)
        self.assertEqual(r['imported'],1)
        self.assertEqual(events,[{'title':'Песня','stage':'Uploading'}])
        self.assertEqual(list((self.root/'backend-jobs').iterdir()),[])
    def test_cancel_signals_worker_and_accepts_its_completed_current_track(self):
        stop=threading.Event();stop.set()
        r=run_backend('transfer',{'wait_stop':True},stop=stop)
        self.assertEqual(r,{'imported':1,'cancelled':True})
    def test_worker_crash_is_reported_instead_of_becoming_an_empty_device_list(self):
        with self.assertRaisesRegex(RuntimeError,'without a result.*7'):
            run_backend('scan',{'crash':True})


if __name__=='__main__':unittest.main()
