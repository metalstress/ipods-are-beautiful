"""Headless device backend. This entry point never initializes Qt."""
import json
import os
from pathlib import Path
import traceback


class FileStop:
    def __init__(self,path):self.path=path
    def is_set(self):return self.path.exists()


def execute(directory):
    directory=Path(directory)
    request=json.loads((directory/'request.json').read_text(encoding='utf-8'))
    try:
        if request['command']=='scan':
            from devices import scan_devices
            from modern_devices import PROFILES,load_profiles
            result={'devices':scan_devices(request['config']),
                    'diagnostics':{'profiles_loaded':len(load_profiles()),'profile_path':str(PROFILES),
                                   'worker_pid':os.getpid(),'qt_loaded':any(m.startswith('PySide6') for m in __import__('sys').modules)}}
        elif request['command']=='transfer':
            from modern_engine import ModernEngine
            with (directory/'events.jsonl').open('a',encoding='utf-8',buffering=1) as events:
                def emit(event):events.write(json.dumps(event,ensure_ascii=False)+'\n')
                result=ModernEngine(request['config'],emit,FileStop(directory/'stop')).transfer(request['paths'])
        else:
            raise ValueError('Unknown backend command')
    except Exception as error:
        result={'error':str(error) or type(error).__name__}
        from modern_devices import record_failure
        record_failure('Headless backend',error)
        (directory/'error.txt').write_text(traceback.format_exc(),encoding='utf-8')
    temporary=directory/'result.part'
    temporary.write_text(json.dumps(result,ensure_ascii=False),encoding='utf-8')
    temporary.replace(directory/'result.json')


if __name__=='__main__':
    import sys
    execute(sys.argv[1])
