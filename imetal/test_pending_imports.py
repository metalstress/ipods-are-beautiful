import json
from pathlib import Path
import plistlib
import tempfile
import unittest
from pending_imports import interrupted_imports


class PendingImportTests(unittest.TestCase):
    def test_only_journal_owned_empty_unfinished_import_is_removed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);directory=root/'before';directory.mkdir()
            source=root/'track.m4a';source.write_bytes(b'audio')
            report={'pid':'42','state':'uploading','source':str(source)}
            (directory/'transfer.json').write_text(json.dumps(report),encoding='utf-8')
            (directory/'operation.plist').write_bytes(plistlib.dumps({'operations':[
                {'operation':'insert_track','pid':42,'item':{'title':'Song','total_time_ms':1000}}]}))
            row={'sync_id':42,'item_pid':9,'title':'Song','total_time_ms':1000,'file_size':0,'location':''}
            self.assertEqual([r['pid'] for r in interrupted_imports({'tracks':[row]},[root])],[42])
            for change in ({'file_size':1},{'location':'F00/song.m4a'},{'title':'Other'},{'sync_id':43}):
                self.assertEqual(interrupted_imports({'tracks':[{**row,**change}]},[root]),[])
            report['state']='verified'
            (directory/'transfer.json').write_text(json.dumps(report),encoding='utf-8')
            self.assertEqual(interrupted_imports({'tracks':[row]},[root]),[])


if __name__=='__main__':unittest.main()
