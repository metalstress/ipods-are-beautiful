"""Run Apple networking outside the GUI process and pass structured results."""
import json
from pathlib import Path
import subprocess
import sys
import time
import uuid

from transport import DATA


def worker_command(directory):
    if getattr(sys,'frozen',False):
        return [sys.executable,'--backend-job',str(directory)]
    return [sys.executable,str(Path(__file__).with_name('app.py')),'--backend-job',str(directory)]


def run_backend(command,config,paths=None,emit=lambda event:None,stop=None,payload=None):
    jobs=DATA/'backend-jobs'
    jobs.mkdir(parents=True,exist_ok=True)
    directory=jobs/uuid.uuid4().hex
    directory.mkdir()
    request={'command':command,'config':config,'paths':[str(p) for p in paths or []]}
    if payload is not None:
        request['payload']=payload
    (directory/'request.json').write_text(json.dumps(request),encoding='utf-8')
    pending='';offset=0;cancelled=False
    def drain():
        nonlocal pending,offset
        path=directory/'events.jsonl'
        if not path.exists():return
        with path.open('r',encoding='utf-8') as events:
            events.seek(offset);pending+=events.read();offset=events.tell()
        lines=pending.split('\n');pending=lines.pop()
        for line in lines:
            if line:emit(json.loads(line))
    process=None
    try:
        process=subprocess.Popen(worker_command(directory),stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0),
            cwd=str(Path(sys.executable).parent))
        deadline=time.monotonic()+75
        while process.poll() is None:
            drain()
            if stop is not None and stop.is_set() and not cancelled:
                (directory/'stop').touch();cancelled=True
            if command=='scan' and time.monotonic()>deadline:
                process.terminate();process.wait(timeout=10)
                raise TimeoutError('Device discovery did not finish.')
            # A transfer is never forcibly killed: cancellation lets its
            # current native commit finish before exiting the worker.
            time.sleep(.05)
        drain()
        result_path=directory/'result.json'
        if not result_path.exists():
            raise RuntimeError(f'Device backend exited without a result ({process.returncode}).')
        result=json.loads(result_path.read_text(encoding='utf-8'))
        if command=='scan':
            (DATA/'last-backend-scan.json').write_text(json.dumps({
                'devices':[{k:d.get(k) for k in ('id','kind','name','mode')} for d in result.get('devices',[])],
                'error':result.get('error'),'worker_exit':process.returncode,
                'diagnostics':result.get('diagnostics')},indent=2),encoding='utf-8')
        return result
    finally:
        # Only remove known files inside this newly-created job directory.
        # Preserve error.txt and its request for diagnosis after a failure.
        if directory.resolve().parent==jobs.resolve() and not (directory/'error.txt').exists():
            for name in ('request.json','events.jsonl','result.json','result.part','stop'):
                (directory/name).unlink(missing_ok=True)
            directory.rmdir()
