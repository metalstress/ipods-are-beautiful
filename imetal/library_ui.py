"""Library navigation and dialogs, using the same headless device worker as uploads."""
from collections import deque
from pathlib import Path
import threading

from PySide6.QtCore import Qt, Signal, QThread, QTimer
from PySide6.QtGui import QColor, QPainter, QPixmap, QIntValidator
from PySide6.QtWidgets import (QWidget, QScrollArea, QFrame, QStackedWidget,
    QHBoxLayout, QVBoxLayout, QFileDialog, QSizePolicy)

from backend_client import run_backend
from library_widgets import (BarButton, LibraryToolbar, LibraryScrollBar, TabBar, TabTransition,
    DarkDialog, PlaylistWheel, input_field, text)
from library_canvas import LibraryCanvas, album_cover_key
from ui import layout


ONLINE_COVER_LIMITS = {'deezer': 4, 'archive': 3}


class LibraryJob(QThread):
    result=Signal(dict)
    event=Signal(dict)
    def __init__(self, config, task, payload):
        super().__init__();self.config=dict(config);self.task=task;self.payload=payload;self.stop=threading.Event()

    def call(self,command,payload=None):
        result=run_backend(command,self.config,payload=payload,emit=self.event.emit,stop=self.stop)
        if result.get('error'):
            raise RuntimeError(result['error'])
        return result

    def cover(self):
        if self.stop.is_set():
            return {}
        album = self.payload.get('album')
        if album is not None:
            from online_artwork import find_cover
            return {'data': find_cover(album, self.stop, self.payload['provider'])}
        payload = {
            'track_id': self.payload['track_id'], 'snapshot_id': self.payload['snapshot_id'],
        }
        if self.payload.get('backend') == 'touch':
            from library_backend import LibraryService
            return LibraryService().execute('library.artwork', self.config, payload, stop=self.stop)
        return self.call('library.artwork', payload)

    def run(self):
        try:
            if self.task=='cover':
                result=self.cover()
            else:
                changed_covers = {}
                if self.task=='read':summary=self.call('library.read')
                else:
                    summary={'snapshot_id':self.payload['snapshot_id']}
                    for spec in self.payload['operations']:
                        proposal=self.call('library.plan',dict(spec,snapshot_id=summary['snapshot_id']))
                        if self.stop.is_set():raise RuntimeError('Library operation cancelled.')
                        summary=self.call('library.apply',{'plan_id':proposal['plan_id']})
                        if spec['action'] == 'replace_artwork':
                            from library_artwork import cover_bytes
                            from library_backend import LibraryCache
                            from library_model import LibraryError
                            try:
                                plan = LibraryCache().load('plans', proposal['plan_id'])
                                source = cover_bytes(plan['cover'])
                            except (OSError, LibraryError):
                                source = None
                            changed_covers.update(dict.fromkeys(proposal['track_ids'], source))
                result=self.call('library.browse',{'snapshot_id':summary['snapshot_id']})
                if changed_covers:
                    result['changed_covers'] = changed_covers
            self.result.emit(result)
        except Exception as error:
            self.result.emit({'error':str(error) or type(error).__name__})


class Overlay(QWidget):
    canceled=Signal()
    def __init__(self,parent,dialog,dim=True):
        super().__init__(parent);self.dim=dim;self.dialog=dialog;dialog.setParent(self)
        self.setGeometry(parent.rect());self.raise_();self.show();dialog.show();dialog.adjustSize();self.arrange()

    def arrange(self):
        self.dialog.move((self.width()-self.dialog.width())//2,(self.height()-self.dialog.height())//2)

    def resizeEvent(self,event):self.arrange()

    def paintEvent(self,event):
        if self.dim:
            p=QPainter(self);p.fillRect(self.rect(),QColor(0,0,0,64))

    def keyPressEvent(self,event):
        if event.key()==Qt.Key_Escape:self.canceled.emit()
        else:super().keyPressEvent(event)


class LibraryPanel(QWidget):
    def __init__(self,owner):
        super().__init__();self.owner=owner;self.device=None;self.config={};self.data=None
        self.view='albums';self.artist=None;self.playlist=None;self.expanded=None;self.history=[]
        self.selecting=False;self.selected=set();self.editing=False;self.row_actions=None
        self.playlist_edit=False;self.draft=None;self.before_draft=None;self.overlay=None
        self.active=None;self.queue=deque();self.generation=0;self.cover_requests=set()
        self.online_jobs = {}
        self.empty=False;self.state_callback=self.reload;self.info_cover=None
        self.setStyleSheet('LibraryPanel {background:#464646;}')
        box=layout(self);self.toolbar=LibraryToolbar();box.addWidget(self.toolbar)
        self.middle=QWidget();middle=layout(self.middle);self.pages=QStackedWidget();middle.addWidget(self.pages)
        self.scroll=QScrollArea();self.scroll.setWidgetResizable(True);self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff);self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setStyleSheet('QScrollArea,QScrollArea>QWidget>QWidget{background:#464646;border:0;}')
        self.canvas=LibraryCanvas();self.scroll.setWidget(self.canvas);self.pages.addWidget(self.scroll)
        self.state=QWidget();self.state.setObjectName('libraryState')
        self.state.setStyleSheet('#libraryState {background:#464646;}')
        state_box=layout(self.state);state_box.addStretch()
        self.state_label=text('',14);self.state_label.setAlignment(Qt.AlignCenter);state_box.addWidget(self.state_label)
        self.retry=BarButton('Retry');self.retry.clicked.connect(lambda:self.state_callback());state_box.addWidget(self.retry,0,Qt.AlignHCenter);state_box.addStretch();self.pages.addWidget(self.state)
        box.addWidget(self.middle,1);self.tabs=TabBar();box.addWidget(self.tabs)
        self.bar=LibraryScrollBar(self.middle);self.bar.hide();self.bar.raise_()
        self.tab_transition = TabTransition(self.middle)
        sb=self.scroll.verticalScrollBar();sb.rangeChanged.connect(self.sync_bar);sb.valueChanged.connect(self.bar.setValue);self.bar.valueChanged.connect(sb.setValue)
        self.cover_timer = QTimer(self)
        self.cover_timer.setSingleShot(True)
        self.cover_timer.setInterval(60)
        self.cover_timer.timeout.connect(self.prefetch_album_covers)
        sb.valueChanged.connect(lambda _: self.cover_timer.start())
        self.tabs.changed.connect(self.set_tab);self.canvas.action.connect(self.item_action);self.canvas.covers_needed.connect(self.request_covers)
        self.inline_actions=QWidget(self.canvas);ia=layout(self.inline_actions,False,spacing=8)
        self.rename=BarButton('Rename');self.remove_playlist=BarButton('Delete',True)
        ia.addWidget(self.rename);ia.addWidget(self.remove_playlist);self.inline_actions.hide()
        self.rename.clicked.connect(self.rename_playlist);self.remove_playlist.clicked.connect(self.delete_playlist)
        self.playlist_actions=QWidget();pa=layout(self.playlist_actions,False,margins=(12,12,12,8),spacing=10)
        self.add_songs=BarButton('Add Songs');self.remove_songs=BarButton('Remove')
        pa.addWidget(self.add_songs);pa.addWidget(self.remove_songs);pa.addStretch()
        self.playlist_actions.setFixedHeight(49);self.playlist_actions.hide();box.insertWidget(1,self.playlist_actions)
        self.add_songs.clicked.connect(self.begin_add_songs);self.remove_songs.clicked.connect(self.remove_draft_songs)

    @property
    def busy(self):return bool(self.active and self.active.task!='cover')

    def resizeEvent(self,event):
        self.tab_transition.finish()
        self.sync_bar()
        if self.overlay and self.overlay.parent() is self:self.overlay.setGeometry(self.rect())

    def sync_bar(self,*args):
        sb=self.scroll.verticalScrollBar();self.bar.setGeometry(self.middle.width()-12,0,12,self.middle.height())
        self.bar.setRange(sb.minimum(),sb.maximum());self.bar.setPageStep(sb.pageStep());self.bar.setValue(sb.value())
        self.bar.setVisible(self.pages.currentWidget() is self.scroll and sb.maximum()>0)
        changed=self.canvas.overflow!=(sb.maximum()>0);self.canvas.overflow=sb.maximum()>0
        if changed:self.canvas.update()
        self.cover_timer.start()

    def status(self,value):
        import re
        value=re.sub(r'\x1b\[[0-9;]*m','',value)
        self.owner.status.setToolTip(value)
        self.owner.status.setText(self.owner.status.fontMetrics().elidedText(value,Qt.ElideRight,max(100,self.owner.status.width())))

    def open_manager(self):
        if self.owner.is_busy():self.status('Transfer in progress');return
        devices=[d for d in self.owner.devices.values() if d.get('kind') in ('touch','iphone')]
        if not devices:self.status('No device found');return
        if len(devices)==1:self.open_device(devices[0]);return
        dialog=DarkDialog(276);dialog.body.addWidget(text('Choose device',18,True),0,Qt.AlignHCenter)
        for d in devices:
            b=BarButton(d['name']);dialog.body.addWidget(b);b.clicked.connect(lambda checked=False,item=d:self.open_device(item))
        cancel=BarButton('Cancel');cancel.clicked.connect(self.close_overlay);dialog.body.addWidget(cancel)
        self.show_dialog(dialog,parent=self.owner.content_stack,dim=False)

    def open_device(self,info):
        self.close_overlay();self.generation+=1;self.queue.clear()
        if self.active:self.active.stop.set()
        self.device=info;self.config=dict(info.get('config') or self.owner.config_now())
        self.data=None;self.view='albums';self.artist=None;self.playlist=None;self.expanded=None
        self.selecting=False;self.selected.clear();self.editing=False;self.playlist_edit=False;self.row_actions=None
        self.owner.content_stack.setCurrentWidget(self);self.reload()

    def reload(self):
        if not self.device:return
        self.show_state('Loading library…');self.enqueue('read',{},True)

    def show_state(self,value,retry=False,empty=False,callback=None,caption='Retry'):
        self.tab_transition.finish()
        self.empty=empty;self.state_callback=callback or self.reload;self.retry.setText(caption)
        self.state_label.setText(value);self.retry.setVisible(retry);self.pages.setCurrentWidget(self.state)
        self.bar.hide();self.update_toolbar()

    def enqueue(self,task,payload,priority=False):
        record=(task,payload,self.generation)
        if priority:
            self.cancel_pending_covers()
            self.queue.appendleft(record)
        else:
            self.queue.append(record)
        self.pump()

    def pump(self):
        if self.owner.quitting or not self.queue:return
        if self.owner.pause_artwork_repair():
            QTimer.singleShot(250,self.pump)
            return
        if self.owner.is_busy():
            QTimer.singleShot(500,self.pump)
            return
        for provider, limit in ONLINE_COVER_LIMITS.items():
            slots = limit - sum(p == provider for p in self.online_jobs.values())
            for _ in range(slots):
                record = next((r for r in self.queue if r[1].get('provider') == provider), None)
                if record is None:
                    break
                self.queue.remove(record)
                self.start_job(*record)
        if not self.active:
            record = next((r for r in self.queue if not r[1].get('provider')), None)
            if record is not None:
                self.queue.remove(record)
                self.start_job(*record)

    def start_job(self, task, payload, generation):
        job = LibraryJob(self.config, task, payload)
        provider = payload.get('provider')
        if provider:
            self.online_jobs[job] = provider
        else:
            self.active = job
        self.owner.jobs.append(job)
        job.result.connect(lambda result:self.finished(job,generation,result))
        job.event.connect(self.progress)
        def cleanup():
            if job in self.owner.jobs:self.owner.jobs.remove(job)
            if self.owner.quitting and not self.owner.jobs:
                from PySide6.QtWidgets import QApplication
                QApplication.instance().quit()
            job.deleteLater()
        job.finished.connect(cleanup);job.start();self.update_toolbar()

    def finished(self,job,generation,result):
        if job is self.active:
            self.active = None
        else:
            self.online_jobs.pop(job, None)
        if generation==self.generation and not self.owner.quitting:
            if job.task == 'cover':
                if not job.stop.is_set():
                    self.finish_cover(job, result)
            elif result.get('error'):
                self.status(result['error']);self.owner.status.setToolTip(result['error'])
                self.show_state('Could not read library' if job.task=='read' else 'Could not save changes',True)
            else:
                scroll = self.scroll.verticalScrollBar()
                scroll_position = scroll.value()
                if job.task == 'change':
                    pending = self.reconcile_covers(result, result.pop('changed_covers', {}))
                else:
                    self.clear_covers()
                self.data = result
                self.selected.clear();self.row_actions=None
                if self.expanded and not any(a['id'] == self.expanded for a in result['albums']):
                    self.expanded = None
                if self.playlist and not any(p['id']==self.playlist for p in self.data['playlists']):self.playlist=None;self.view='playlists'
                self.status('Library updated' if job.task=='change' else 'Ready');self.refresh()
                if job.task == 'change' and self.pages.currentWidget() is self.scroll:
                    scroll.setValue(scroll_position)
                    self.request_covers(pending, device_only=True)
                self.owner.lcd.display('',active=False)
        self.pump();self.update_toolbar()

    def finish_cover(self, job, result):
        source = result.get('path') or result.get('data')
        if not source:
            provider = job.payload.get('provider')
            if provider == 'deezer':
                fallback = dict(job.payload, provider='archive')
                self.queue.append(('cover', fallback, self.generation))
            elif provider == 'archive' and job.payload.get('track_id'):
                fallback = dict(job.payload)
                del fallback['album']
                del fallback['provider']
                self.queue.append(('cover', fallback, self.generation))
            return

        self.show_cover(job.payload['key'], source, result.get('track_id'))

    def show_cover(self, key, source, track_id=None):
        self.canvas.cover_sources[key] = source
        self.canvas.covers.pop(key, None)
        if track_id:
            self.canvas.cover_sources[track_id] = source
            self.canvas.covers.pop(track_id, None)
        self.canvas.update()
        if self.info_cover and self.info_cover[0] == key:
            self.info_cover[1].pixmap = self.canvas.load_cover(key)
            self.info_cover[1].update()

    def progress(self,event):
        if event.get('type')=='library_change':
            stage=event.get('stage','Saving changes');self.status(stage)
            self.owner.lcd.display(stage,'',event.get('completed',0)/max(1,event.get('total',1)),True)

    def change(self,operations):
        if not self.data or self.busy:return
        expanded=[]
        for spec in operations:
            ids=spec.get('track_ids',[])
            if spec['action'] in ('delete_tracks','update_metadata','replace_artwork') and len(ids)>499:
                expanded.extend(dict(spec,track_ids=ids[i:i+499]) for i in range(0,len(ids),499))
            else:expanded.append(spec)
        self.close_overlay();self.status('Saving changes…')
        self.enqueue('change',{'snapshot_id':self.data['snapshot_id'],'operations':expanded},True)

    def prefetch_album_covers(self):
        keys = []
        if self.view == 'albums' and self.pages.currentWidget() is self.scroll:
            top = self.scroll.verticalScrollBar().value()
            keys = self.canvas.nearby_album_cover_keys(top, self.scroll.viewport().height())
        if self.info_cover and self.info_cover[0].startswith('album:'):
            keys.insert(0, self.info_cover[0])
        self.request_covers(keys, prioritize_albums=True)

    def request_covers(self, ids, *, prioritize_albums=False, device_only=False):
        if (not self.data or not self.device or self.owner.preview or self.owner.quitting
                or self.owner.content_stack.currentWidget() is not self):
            return
        if self.busy or any(task != 'cover' for task, _, _ in self.queue):
            self.canvas.asked.difference_update(ids)
            return
        for ident in ids:
            if ident in self.cover_requests or ident in self.canvas.cover_sources:
                continue
            payload = {'key': ident, 'snapshot_id': self.data['snapshot_id'], 'track_id': ident,
                       'backend': self.data.get('backend')}
            if ident.startswith('album:'):
                album = next((row for row in self.data['albums'] if album_cover_key(row['id']) == ident), None)
                if album is None:
                    continue
                if album.get('artwork_track_id') in self.canvas.cover_sources:
                    continue
                payload['track_id'] = album.get('artwork_track_id')
                if not device_only:
                    payload.update(album=album, provider='deezer')
            track_id = payload['track_id']
            if device_only and not track_id:
                continue
            if track_id and self.data.get('device_id'):
                from library_covers import artwork_path
                path = artwork_path(self.data['device_id'], self.data['revision'], track_id, self.data['backend'])
                if path.is_file():
                    self.show_cover(ident, str(path), track_id)
                    continue
            self.cover_requests.add(ident)
            self.queue.append(('cover', payload, self.generation))
        if prioritize_albums:
            ranks = {key: rank for rank, key in enumerate(dict.fromkeys(ids))}
            kept = []
            for record in self.queue:
                key = record[1].get('key', '')
                if key.startswith('album:') and key not in ranks:
                    self.cover_requests.discard(key)
                else:
                    kept.append(record)
            self.queue = deque(sorted(kept, key=lambda record: ranks.get(record[1].get('key'), -1)))
        self.pump()

    def cancel_pending_covers(self):
        cancelled = {payload['key'] for task, payload, _ in self.queue if task == 'cover'}
        for job in self.online_jobs:
            job.stop.set()
            cancelled.add(job.payload['key'])
        self.queue = deque(record for record in self.queue if record[0] != 'cover')
        self.cover_requests.difference_update(cancelled)
        self.canvas.asked.difference_update(cancelled)

    def reconcile_covers(self, result, changed):
        old_artwork_tracks = {a['id']: a.get('artwork_track_id') for a in self.data['albums']}
        valid = {track['id'] for track in result['tracks']}
        invalidated = set(changed)
        updates = {key: source for key, source in changed.items() if key in valid}
        for album in result['albums']:
            key = album_cover_key(album['id'])
            track_id = album.get('artwork_track_id')
            valid.add(key)
            if track_id in changed:
                updates[key] = changed[track_id]
                invalidated.add(key)
            elif old_artwork_tracks.get(album['id']) != track_id:
                invalidated.add(key)
        keep = valid - invalidated
        for covers in (self.canvas.covers, self.canvas.cover_sources):
            for key in covers.keys() - keep:
                del covers[key]
        self.cover_requests.intersection_update(keep)
        self.canvas.asked.intersection_update(keep)
        for key, source in updates.items():
            if source:
                self.canvas.cover_sources[key] = source
        return [key for key, source in updates.items() if source is None]

    def accept_artwork_repair(self, repair):
        result = repair.get('library')
        if not self.data or not result or result['device_id'] != self.data.get('device_id'):
            return
        previous = self.data['snapshot_id']
        position = self.scroll.verticalScrollBar().value()
        self.reconcile_covers(result, repair.get('changed_covers', {}))
        self.data = result
        # A user action queued while the idle job finished must bind to the
        # verified cover update rather than its now-stale predecessor.
        self.queue = deque((task, dict(payload, snapshot_id=result['snapshot_id'])
                            if task == 'change' and payload.get('snapshot_id') == previous else payload, generation)
                           for task, payload, generation in self.queue)
        self.refresh()
        self.scroll.verticalScrollBar().setValue(position)

    def clear_covers(self):
        self.cancel_pending_covers()
        self.canvas.covers.clear()
        self.canvas.cover_sources.clear()
        self.canvas.asked.clear()
        self.cover_requests.clear()

    def close_overlay(self):
        self.info_cover=None
        if self.overlay:self.overlay.hide();self.overlay.deleteLater();self.overlay=None

    def show_dialog(self,dialog,parent=None,dim=False):
        self.owner.pause_artwork_repair()
        self.close_overlay();self.overlay=Overlay(parent or self,dialog,dim);self.overlay.canceled.connect(self.close_overlay)

    def close_library(self):
        self.tab_transition.finish()
        self.close_overlay();self.generation+=1;self.queue.clear()
        if self.active:self.active.stop.set()
        self.clear_covers()
        self.owner.content_stack.setCurrentIndex(0);self.owner.lcd.display('',active=False)

    def device_removed(self,ident):
        if self.device and self.device['id']==ident:
            self.generation+=1;self.queue.clear()
            if self.active:self.active.stop.set()
            self.clear_covers()
            self.close_overlay();self.show_state('Device disconnected');self.device=None

    def set_tab(self,key):
        if self.busy:return
        previous = 'playlists' if self.view in ('playlist', 'add_songs') else self.view
        snapshot = self.middle.grab() if previous != key and self.data and self.isVisible() else None
        self.view=key;self.artist=None;self.playlist=None;self.expanded=None;self.selecting=False;self.selected.clear()
        self.editing=False;self.playlist_edit=False;self.row_actions=None;self.draft=None;self.history=[];self.refresh()
        if snapshot is not None:
            order = list(self.tabs.buttons)
            self.tab_transition.begin(snapshot, 1 if order.index(key) > order.index(previous) else -1)

    def playlist_record(self):
        return next((p for p in (self.data or {}).get('playlists',[]) if p['id']==self.playlist),None)

    def track_lookup(self):return {t['id']:t for t in self.data['tracks']}

    def refresh(self):
        if not self.data:return
        tracks=self.track_lookup();albums={}
        if self.data.get('backend') == 'iphone':
            tracks = {ident: track for ident, track in tracks.items() if track['availability'] == 'local'}
        for t in tracks.values():albums.setdefault(t['album_id'],[]).append(t)
        for rows in albums.values():rows.sort(key=lambda t:(t['disc_number'],t['track_number'],t['title'].casefold()))
        if self.view=='albums':
            rows=[r for r in self.data['albums'] if (self.data.get('backend') != 'iphone' or r['id'] in albums) and
                  (not self.artist or any(t['artist_id']==self.artist for t in albums[r['id']]))]
        elif self.view=='artists':
            rows=[dict(r,album_count=len({t['album_id'] for t in tracks.values() if t['artist_id']==r['id']}),
                       track_count=sum(t['artist_id']==r['id'] for t in tracks.values()))
                  for r in self.data['artists'] if any(t['artist_id']==r['id'] for t in tracks.values())]
        elif self.view=='playlists':rows=self.data['playlists']
        elif self.view=='playlist':
            record=self.playlist_record();members=self.draft if self.playlist_edit else (record or {}).get('members')
            if members is None:self.show_state('Playlist unavailable',True);return
            rows=[tracks[i] for i in members if i in tracks]
        else:rows=list(tracks.values())
        self.empty=False
        self.tabs.select('playlists' if self.view in ('playlist','add_songs') else self.view)
        if not rows:
            editable=self.view=='playlist' and (self.playlist_record() or {}).get('can_plan_delete')
            def add_empty():
                if not self.playlist_edit:self.begin_playlist_edit()
                self.begin_add_songs()
            self.playlist_actions.hide();self.inline_actions.hide()
            self.show_state('' if editable else {'playlists':'No playlists','albums':'No albums','artists':'No artists'}.get(self.view,'No songs'),
                bool(editable),True,add_empty if editable else None,'Add Songs' if editable else 'Retry')
            return
        self.canvas.configure(self.view,rows,tracks,albums,selecting=self.selecting,selected=self.selected,
            expanded=self.expanded,editing=self.editing,row_actions=self.row_actions,playlist_edit=self.playlist_edit)
        self.pages.setCurrentWidget(self.scroll)
        self.playlist_actions.setVisible(self.playlist_edit and self.view=='playlist')
        self.remove_songs.setEnabled(bool(self.selected) and not self.busy)
        self.inline_actions.hide()
        if self.row_actions and self.editing:
            block=next((b for b in self.canvas.blocks if b.get('row',{}).get('id')==self.row_actions),None)
            if block:
                self.inline_actions.setGeometry(self.canvas.width()-166,block['y']+12,154,29);self.inline_actions.show();self.inline_actions.raise_()
        self.tabs.select('playlists' if self.view in ('playlist','add_songs') else self.view)
        self.update_toolbar();QTimer.singleShot(0,self.sync_bar)

    def update_toolbar(self):
        title=('iPod Touch Library' if (self.device or {}).get('kind')=='touch' else (self.device or {}).get('name','Music')+' Library')
        if self.artist and self.data:title=next((r['title'] for r in self.data['artists'] if r['id']==self.artist),title)
        if self.playlist and self.playlist_record():title=self.playlist_record()['name']
        left=('Back',self.back);right=[]
        if self.data and (self.pages.currentWidget() is self.scroll or self.empty):
            if self.view=='add_songs':
                title='Add Songs';left=('Cancel',self.cancel_add_songs);right=[('Add',self.accept_add_songs,False,bool(self.selected))]
            elif self.playlist_edit:
                left=('Cancel',self.cancel_playlist_edit);right=[('Done',self.save_playlist,False,not self.busy)]
            elif self.view=='playlists':
                right=([('Cancel',self.cancel_editing)] if self.editing else [('Edit',self.begin_editing)])+[('New',self.new_playlist)]
            elif self.view=='playlist':
                if (self.playlist_record() or {}).get('can_plan_delete'):right=[('Edit',self.begin_playlist_edit)]
            elif self.selecting:
                left=('Cancel',self.cancel_selection)
                if self.view=='tracks' and self.selected:right.append(('Add to playlist',self.pick_playlist))
                right.append(('Delete',self.confirm_delete,True,bool(self.selected)))
            elif self.view in ('albums','tracks'):
                if self.expanded:right.append(('Info',self.album_info))
                right.append(('Select',self.begin_selection))
        self.toolbar.configure(title,left,right)
        for b in self.toolbar.right:b.setEnabled(b.isEnabled() and not self.busy)
        self.inline_actions.setEnabled(not self.busy)

    def back(self):
        if self.playlist or self.artist:self.set_tab('playlists' if self.playlist else 'artists')
        else:self.close_library()

    def begin_selection(self):self.selecting=True;self.selected.clear();self.refresh()
    def cancel_selection(self):self.selecting=False;self.selected.clear();self.refresh()
    def begin_editing(self):self.editing=True;self.refresh()
    def cancel_editing(self):self.editing=False;self.row_actions=None;self.refresh()

    def item_action(self,event):
        if self.busy:return
        ident=event['id'];action=event['action']
        if action=='playlist_actions':self.row_actions=None if self.row_actions==ident else ident;self.refresh();return
        if action=='artist':self.artist=ident;self.view='albums';self.refresh();return
        if action=='playlist':self.playlist=ident;self.view='playlist';self.refresh();return
        if action=='album':
            if self.selecting:
                ids={t['id'] for t in self.canvas.album_tracks[ident]}
                if ids<=self.selected:self.selected-=ids
                else:self.selected|=ids
                self.refresh()
            else:
                self.expanded = None if self.expanded == ident else ident
                self.canvas.expand_album(self.expanded)
                self.update_toolbar()
            return
        if action=='track':
            if self.selecting or self.playlist_edit or self.view=='add_songs':
                if not self.track_lookup()[ident].get('can_plan_delete'):
                    self.status('Cloud or protected songs are read-only.');return
                if ident in self.selected:self.selected.remove(ident)
                else:self.selected.add(ident)
                self.refresh()
            else:self.info([ident],False)

    def buttons(self,dialog,labels,spacing=12):
        row=QWidget();box=layout(row,False,spacing=spacing);buttons=[]
        for caption,callback in labels:
            b=BarButton(caption);b.clicked.connect(callback);box.addWidget(b,1);buttons.append(b)
        dialog.body.addWidget(row);return buttons

    def name_dialog(self,creating,name='',ident=None):
        dialog=DarkDialog(360);field=input_field(name,'New Playlist');field.setMaxLength(255)
        if creating:dialog.body.addWidget(text('New Playlist',18,True),0,Qt.AlignHCenter)
        dialog.body.addWidget(field)
        def save():
            self.change([{'action':'create_playlist' if creating else 'rename_playlist','name':field.text(),'playlist_id':ident}])
        _,button=self.buttons(dialog,[('Cancel',self.close_overlay),('Create' if creating else 'Save',save)])
        button.setEnabled(bool(field.text().strip()));field.textChanged.connect(lambda value:button.setEnabled(bool(value.strip())))
        field.returnPressed.connect(lambda:save() if button.isEnabled() else None)
        self.show_dialog(dialog);field.setFocus();field.selectAll()

    def new_playlist(self):self.name_dialog(True)
    def rename_playlist(self):
        record=next(p for p in self.data['playlists'] if p['id']==self.row_actions);self.name_dialog(False,record['name'],record['id'])
    def delete_playlist(self):self.change([{'action':'delete_playlist','playlist_id':self.row_actions}])

    def pick_playlist(self):
        rows=[p for p in self.data['playlists'] if p.get('can_plan_delete') and p.get('members') is not None]
        dialog=DarkDialog(200,gloss=False);dialog.body.setContentsMargins(12,14,12,11);dialog.body.setSpacing(9)
        wheel=PlaylistWheel(rows);dialog.body.addWidget(wheel)
        def save():
            self.change([{'action':'add_to_playlist','playlist_id':rows[wheel.index]['id'],'track_ids':sorted(self.selected)}])
        _,button=self.buttons(dialog,[('Cancel',self.close_overlay),('Save',save)],spacing=6);button.setEnabled(bool(rows));self.show_dialog(dialog,dim=True);wheel.setFocus()

    def begin_playlist_edit(self):
        self.playlist_edit=True;self.draft=list(self.playlist_record()['members']);self.before_draft=list(self.draft);self.selected.clear();self.refresh()
    def cancel_playlist_edit(self):self.playlist_edit=False;self.draft=None;self.selected.clear();self.refresh()
    def save_playlist(self):
        spec={'action':'set_playlist_tracks','playlist_id':self.playlist,'track_ids':list(self.draft)}
        self.playlist_edit=False;self.draft=None;self.change([spec])
    def remove_draft_songs(self):self.draft=[i for i in self.draft if i not in self.selected];self.selected.clear();self.refresh()
    def begin_add_songs(self):self.view='add_songs';self.selected.clear();self.refresh()
    def cancel_add_songs(self):self.view='playlist';self.selected.clear();self.refresh()
    def accept_add_songs(self):
        for track in self.data['tracks']:
            if track['id'] in self.selected and track['id'] not in self.draft:self.draft.append(track['id'])
        self.cancel_add_songs()

    def confirm_delete(self):
        full=[rows for rows in self.canvas.album_tracks.values() if rows and {r['id'] for r in rows}<=self.selected]
        complete=self.view=='albums' and sum(len(rows) for rows in full)==len(self.selected)
        title=('Delete album?' if len(full)==1 else f'Delete {len(full)} albums?') if complete else f'Delete {len(self.selected)} songs?'
        dialog=DarkDialog(360);dialog.body.addWidget(text(title,18,True),0,Qt.AlignHCenter)
        ids=sorted(self.selected)
        self.buttons(dialog,[('Cancel',self.close_overlay),('Delete',lambda:self.change([{'action':'delete_tracks','track_ids':ids}]))])
        self.show_dialog(dialog)

    def album_info(self):
        self.info([t['id'] for t in self.canvas.album_tracks.get(self.expanded,[])],True)

    def info(self,identifiers,album):
        tracks=[self.track_lookup()[i] for i in identifiers]
        if not tracks:return
        dialog=DarkDialog(443);dialog.body.setSpacing(6)
        heading=QWidget();hb=layout(heading,False);hb.addWidget(text('Album Info' if album else 'Song Info',18,True));hb.addStretch()
        names=QWidget();nb=layout(names);title=text(tracks[0]['album'] if album else tracks[0]['title'],13,True);title.setAlignment(Qt.AlignRight)
        artist=text(tracks[0]['album_artist'] or tracks[0]['artist'],11,color='#9c9c9c');artist.setAlignment(Qt.AlignRight)
        nb.addWidget(title);nb.addWidget(artist);heading.setFixedHeight(24);dialog.body.addWidget(heading)
        hb.addWidget(names)
        content=QWidget();content.setFixedHeight(200);cb=layout(content,False,spacing=18)
        cover_id = album_cover_key(tracks[0]['album_id']) if album else identifiers[0]
        cover = self.canvas.load_cover(cover_id, identifiers[0])
        class Cover(QWidget):
            def paintEvent(w,event):
                p=QPainter(w);p.setRenderHint(QPainter.Antialiasing)
                from PySide6.QtGui import QPainterPath
                path=QPainterPath();path.addRoundedRect(w.rect(),10,10);p.setClipPath(path)
                if not picture.pixmap.isNull():p.drawPixmap(w.rect(),picture.pixmap)
                else:p.fillRect(w.rect(),QColor('#999999'))
        picture=Cover();picture.pixmap=cover or QPixmap();picture.setFixedSize(200,200);cb.addWidget(picture)
        fields=QWidget();fields.setFixedWidth(192);fb=layout(fields,spacing=6)
        genres={t['genre'] for t in tracks};years={t['year'] for t in tracks}
        genre=input_field(next(iter(genres)) if len(genres)==1 else '')
        genre.setMaxLength(1024)
        year=input_field(str(next(iter(years)) or '') if len(years)==1 else '');year.setValidator(QIntValidator(0,9999,year))
        fb.addWidget(text('Genre',12,color='#b7b7b7'));fb.addWidget(genre);fb.addSpacing(6)
        fb.addWidget(text('Year',12,color='#b7b7b7'));fb.addWidget(year);fb.addSpacing(6)
        artwork=BarButton('Change Artwork');fb.addWidget(artwork);chosen={'path':None};dirty=set()
        genre.textEdited.connect(lambda:dirty.add('genre'));year.textEdited.connect(lambda:dirty.add('year'))
        def pick():
            path,_=QFileDialog.getOpenFileName(self,'Change Artwork',str(Path.home()/'Pictures'),'Images (*.png *.jpg *.jpeg *.webp *.bmp)')
            if path:
                value=QPixmap(path)
                if not value.isNull():
                    self.info_cover=None;chosen['path']=path;picture.pixmap=value;picture.update()
        artwork.clicked.connect(pick)
        actions=QWidget();ab=layout(actions,False,spacing=6);cancel=BarButton('Cancel');save=BarButton('Save');ab.addWidget(cancel,1);ab.addWidget(save,1);fb.addWidget(actions)
        cancel.clicked.connect(self.close_overlay)
        writable=all(t.get('can_plan_delete') for t in tracks)
        save.setEnabled(writable);artwork.setEnabled(writable);genre.setReadOnly(not writable);year.setReadOnly(not writable)
        def commit():
            changes={}
            if 'genre' in dirty and not genre.text().strip():self.status('Enter a genre.');genre.setFocus();return
            if year.text() and not year.hasAcceptableInput():self.status('Enter a year from 0 to 9999.');year.setFocus();return
            if 'genre' in dirty:changes['genre']=genre.text().strip()
            if 'year' in dirty:changes['year']=int(year.text() or 0)
            operations=[]
            if changes:operations.append({'action':'update_metadata','track_ids':identifiers,'changes':changes})
            if chosen['path']:operations.append({'action':'replace_artwork','track_ids':identifiers,'cover_path':chosen['path']})
            if operations:self.change(operations)
            else:self.close_overlay()
        save.clicked.connect(commit);cb.addWidget(fields);dialog.body.addWidget(content);self.show_dialog(dialog)
        self.info_cover=(cover_id,picture)
        if picture.pixmap.isNull():
            self.request_covers([cover_id])
