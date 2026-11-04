"""Identify only our own interrupted, empty native imports from their journals."""
import json
from pathlib import Path
import plistlib


def interrupted_imports(catalogue,roots):
    pending={r['sync_id']:r for r in catalogue['tracks']
             if r['sync_id'] and not r['location'] and r['file_size']==0}
    found={}
    for root in dict.fromkeys(Path(r).resolve() for r in roots):
        for journal in root.glob('*/transfer.json'):
            try:
                report=json.loads(journal.read_text(encoding='utf-8'))
                if report.get('state') not in ('metadata-sent','uploading'):
                    continue
                pid=int(report['pid']);row=pending.get(pid)
                source=Path(report['source'])
                if row is None or not source.is_file():continue
                operations=plistlib.loads(journal.with_name('operation.plist').read_bytes())['operations']
                if catalogue.get('db_pid') is not None and not any(
                    o.get('operation')=='update_db_info' and o.get('pid')==catalogue['db_pid'] for o in operations):
                    continue
                inserts=[o for o in operations if o.get('operation')=='insert_track' and o.get('pid')==pid]
                if len(inserts)!=1:continue
                item=inserts[0]['item']
                if item.get('title')!=row['title']:continue
                duration=item.get('total_time_ms')
                if duration and abs(duration-row['total_time_ms'])>2000:continue
                found[pid]={'pid':pid,'item_pid':row['item_pid'],'title':row['title'],
                            'journal':str(journal),'source':str(source)}
            except (OSError,ValueError,KeyError,TypeError,plistlib.InvalidFileException):
                continue
    return list(found.values())
