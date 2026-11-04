"""Figma-driven native desktop app, with a queue and explicit device selection."""
import argparse
import json
import os
from pathlib import Path
import sys
import threading

if '--render' in sys.argv:
    os.environ['QT_QPA_PLATFORM'] = 'offscreen'

from PySide6.QtCore import Qt, Signal, QThread, QTimer, QLockFile, QRectF, QCoreApplication
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPen, QPainterPath, QRegion, QShortcut, QKeySequence
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QFrame, QVBoxLayout,
    QHBoxLayout, QComboBox, QLineEdit, QTableWidgetItem, QMessageBox, QMenu,
    QScrollArea, QSizePolicy)

from media import discover
from transport import DATA, RESOURCES, settings, save_settings
from devices import scan_devices, connect_device
from theme import apply_light_theme
from version import VERSION
from transfer_speed import TransferSpeed
from ui import (APP_NAME, ASSETS, COLORS, load_fonts, font, label, layout, SilverButton,
    TitleBar, LCD, DropZone, DeviceCard, Svg, AutoCheck, TrackTable, TableFrame, Grip, ConnectionForm, Footer)


def human_size(size):
    return f'{size/1024**3:.2f} GB' if size >= 1024**3 else f'{size/1024**2:.1f} MB'


class Job(QThread):
    event = Signal(dict)
    result = Signal(dict)

    def __init__(self, config, paths=None, scan=False):
        super().__init__()
        self.config = dict(config)
        self.paths = list(paths) if paths is not None else None
        self.scan = scan
        self.stop = threading.Event()

    def run(self):
        try:
            if self.paths is None:
                config = dict(self.config)
                if not self.scan:
                    config['explicit_ip'] = config.get('ip','')
                from backend_client import run_backend
                self.result.emit(run_backend('scan',config,stop=self.stop))
            else:
                if self.config.get('backend') == 'modern':
                    from backend_client import run_backend
                    self.result.emit(run_backend('transfer',self.config,self.paths,self.event.emit,self.stop))
                else:
                    from engine import Engine
                    self.result.emit(Engine(self.config, self.event.emit, self.stop).transfer(self.paths))
        except Exception as error:
            self.result.emit({'error': str(error) or type(error).__name__})


class Shell(QFrame):
    def __init__(self):
        super().__init__()
        self.setObjectName('shell')

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QPen(QColor('#7b8084'), .5))
        p.setBrush(QColor('#d6d6d6'))
        p.drawRoundedRect(QRectF(self.rect()).adjusted(.25,.25,-.25,-.25),15,15)


class Window(QMainWindow):
    def __init__(self, preview=False):
        super().__init__()
        self.preview = preview
        self.config = settings()
        self.paths = []
        self.rows = {}
        self.states = {}
        self.metadata = {}
        self.attempted = set()
        self.devices = {}
        self.cards = {}
        self.jobs = []
        self.worker = None
        self.checking = False
        self.scan_serial = 0
        self.quitting = False
        self.current_device = None
        self.batch_paths = []
        self.last_progress = {}
        self.speed = TransferSpeed()
        self.progress_timer = QTimer(self)
        self.progress_timer.setInterval(500)
        self.progress_timer.timeout.connect(self.refresh_speed)
        self.current_device_kind = 'touch'
        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(QIcon(str(RESOURCES / 'imetal.ico')))
        self.setWindowFlags(Qt.Window | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.resize(910, 661)
        self.setMinimumSize(790, 600)
        self.setFont(font(10))
        self.setStyleSheet('''
          QToolTip {background:#ffffe5;border:1px solid #a5a5a5;color:#484848;padding:4px;}
          QMenu {background:#f6f6f6;border:1px solid #afafaf;color:#484848;}
          QMenu::item:selected {background:#d6d6d6;}
          QComboBox,QLineEdit {font-family:Tahoma;font-size:11px;background:white;
            color:#484848;border:1px solid #acacac;border-radius:4px;padding:4px;}
          QComboBox::drop-down {border:0;width:21px;}
          QComboBox::down-arrow {image:url(DROPDOWN);width:11px;height:9px;}
          QComboBox QAbstractItemView {background:white;color:#484848;
            selection-background-color:#d6d6d6;selection-color:#484848;outline:0;}
          QLineEdit:focus,QComboBox:focus {border-color:#838383;}
          QLineEdit:disabled,QComboBox:disabled {color:#999999;background:#e5e5e5;}
        '''.replace('DROPDOWN', str(ASSETS / 'dropdown.svg').replace('\\','/')))
        shell = Shell()
        self.shell = shell
        self.setCentralWidget(shell)
        outer = layout(shell, margins=(1,1,1,1))
        outer.addWidget(TitleBar(self))
        toolbar = QFrame()
        toolbar.setFixedHeight(81)
        toolbar.setObjectName('toolbar')
        toolbar.setStyleSheet('QFrame#toolbar {background:#e4e4e4;border:0;border-bottom:1px solid rgba(152,152,152,128);}')
        bar = layout(toolbar, False, (12,0,12,12))
        self.lcd = LCD()
        bar.addWidget(self.lcd)
        outer.addWidget(toolbar)
        body = QWidget()
        main_body = layout(body, False)
        sidebar = QFrame()
        sidebar.setFixedWidth(221)
        sidebar.setObjectName('sidebar')
        sidebar.setStyleSheet('QFrame#sidebar {background:#ededed;border:0;border-right:1px solid rgba(154,173,185,128);}')
        side = layout(sidebar, spacing=0)
        form = ConnectionForm()
        form.setFixedHeight(214)
        form.setObjectName('connectionForm')
        form.setStyleSheet('QFrame#connectionForm {background:#ededed;border:0;border-bottom:1px solid rgba(152,152,152,128);}')
        fields = layout(form, margins=(12,12,12,12), spacing=8)
        fields.setAlignment(Qt.AlignTop)
        note = label('Device needs OpenSSH to be installed\nin order to upload music via Wi-Fi.', color=COLORS['muted'], height=24)
        self.connection_note = note
        fields.addWidget(note)
        self.scan_button = SilverButton('Scan')
        self.scan_button.clicked.connect(self.scan)
        fields.addWidget(self.scan_button)
        separator = QFrame()
        separator.setFixedHeight(1)
        fields.addWidget(separator)
        form.separator_marker=separator
        self.mode = QComboBox()
        self.mode.setFixedHeight(27)
        for title, key in [('Auto: USB / Wi-Fi','auto'),('USB only','usb'),('Wi-Fi only','wifi')]:
            self.mode.addItem(title,key)
        self.mode.setCurrentIndex(max(0,self.mode.findData(self.config.get('mode','auto'))))
        fields.addWidget(self.mode)
        self.ip = QLineEdit()
        self.ip.setFixedHeight(27)
        self.ip.setAlignment(Qt.AlignCenter)
        self.ip.setPlaceholderText('local iP of your device')
        palette = self.ip.palette()
        from PySide6.QtGui import QPalette
        palette.setColor(QPalette.PlaceholderText, QColor('#afafaf'))
        self.ip.setPalette(palette)
        self.ip.setToolTip('Settings → Wi-Fi → your network → IP address')
        self.ip.returnPressed.connect(self.connect)
        fields.addWidget(self.ip)
        self.connect_button = SilverButton('Connect')
        self.connect_button.clicked.connect(self.connect)
        fields.addWidget(self.connect_button)
        help_link = label('<a href="help" style="color:#848484;text-decoration:underline">How to connect?</a>',color=COLORS['muted'])
        help_link.linkActivated.connect(self.connection_help)
        fields.addWidget(help_link)
        side.addWidget(form)
        side.addSpacing(8)
        device_area = QWidget()
        device_box = layout(device_area, margins=(12,0,9,0), spacing=8)
        device_box.addWidget(label('Connected devices',bold=True))
        self.device_scroll = QScrollArea()
        self.device_scroll.setWidgetResizable(True)
        self.device_scroll.setFrameShape(QFrame.NoFrame)
        self.device_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.device_scroll.setStyleSheet('QScrollArea {border:0;background:transparent;} QScrollBar:vertical{width:7px;}')
        self.device_container = QWidget()
        self.device_layout = layout(self.device_container, spacing=8)
        self.device_layout.setAlignment(Qt.AlignTop)
        self.device_scroll.setWidget(self.device_container)
        device_box.addWidget(self.device_scroll,1)
        side.addWidget(device_area,1)
        hints = QWidget()
        hints.setFixedHeight(40)
        hint_box = layout(hints,margins=(12,0,12,12),spacing=4)
        hint_box.addWidget(label('Music will be uploaded to connected devices.',color=COLORS['muted']))
        hint_row = QWidget()
        hint_line = layout(hint_row,False,spacing=4)
        hint_line.addWidget(label('Eject ',color=COLORS['muted']))
        hint_line.addWidget(Svg('eject-help',9,9))
        hint_line.addWidget(label('to remove device from selection.',color=COLORS['muted']))
        hint_line.addStretch()
        hint_box.addWidget(hint_row)
        side.addWidget(hints)
        main_body.addWidget(sidebar)
        content = QWidget()
        content.setObjectName('musicContent')
        content.setStyleSheet('QWidget#musicContent {background:#d6d6d6;}')
        # The Figma's fixed children overflow its declared bottom padding by 2px:
        # visible bottom gap is 10px at the reference height of 661px.
        main = layout(content,margins=(16,13,16,10),spacing=10)
        self.drop = DropZone()
        self.drop.paths.connect(self.add_paths)
        main.addWidget(self.drop)
        info_row = QWidget()
        info_row.setFixedHeight(12)
        information = layout(info_row,False,spacing=10)
        self.message = label('Unsupported formats are converted automatically.',color=COLORS['muted'])
        self.count = label('0 tracks selected',color=COLORS['muted'])
        information.addWidget(self.message,1)
        information.addWidget(self.count)
        main.addWidget(info_row)
        self.table = TrackTable()
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self.queue_menu)
        self.table_frame = TableFrame(self.table)
        main.addWidget(self.table_frame,1)
        options = QWidget()
        options.setFixedHeight(25)
        controls = layout(options,False)
        self.auto = AutoCheck()
        self.auto.setChecked(self.config.get('auto_upload',True))
        self.auto.toggled.connect(self.auto_changed)
        controls.addWidget(self.auto,1)
        self.upload = SilverButton('Upload')
        self.upload.setFixedWidth(113)
        self.upload.clicked.connect(self.start)
        controls.addWidget(self.upload)
        main.addWidget(options)
        main_body.addWidget(content,1)
        outer.addWidget(body,1)
        footer = Footer()
        footer.setFixedHeight(26)
        footer.setObjectName('footer')
        bottom = layout(footer,False,(12,4,12,4))
        self.status = label('Ready for transfer',color='#626e76')
        bottom.addWidget(self.status,1)
        bottom.addWidget(label(APP_NAME+' '+VERSION,color='#697b89'))
        bottom.addWidget(Grip(self))
        outer.addWidget(footer)
        self.upload.setEnabled(not self.auto.isChecked())
        QShortcut(QKeySequence.Delete,self.table,activated=self.remove_selected)
        QShortcut(QKeySequence('Ctrl+O'),self,activated=self.drop.choose_files)
        QShortcut(QKeySequence('Ctrl+Shift+O'),self,activated=self.drop.choose_folder)
        QShortcut(QKeySequence('Ctrl+Return'),self,activated=self.start)
        if not preview:
            QTimer.singleShot(300,self.scan)

    def toggle_maximized(self):
        self.showNormal() if self.isMaximized() else self.showMaximized()

    def config_now(self):
        self.config.update(mode=self.mode.currentData(),auto_upload=self.auto.isChecked())
        if self.ip.text().strip():
            self.config['ip'] = self.ip.text().strip()
        if not self.preview:
            save_settings(self.config)
        return dict(self.config)

    def connection_help(self):
        QMessageBox.information(self,APP_NAME,
            'USB: connect and unlock the device, trust this computer, then press Scan.\n\n'
            'iPhone 16 Pro / iOS 27: Wi-Fi uses the existing Apple pairing. '
            'Connect by USB first; wireless syncing must be enabled.\n\n'
            'iPod touch: install OpenSSH in Cydia for Wi-Fi, then enter its IP address and press Connect.\n\n'
            'Eject removes a device from selection and finishes its current operation safely.')

    def watch(self,job,handler):
        self.jobs.append(job)
        job.event.connect(self.on_event)
        job.result.connect(handler)
        job.finished.connect(lambda:self.jobs.remove(job) if job in self.jobs else None)
        job.finished.connect(lambda:QApplication.instance().quit() if self.quitting and not self.jobs else None)
        job.start()

    def scan(self):
        self.check_connection(scan=True)

    def connect(self):
        self.check_connection(scan=False)

    def check_connection(self,scan=False):
        if self.checking or self.is_busy():
            return
        self.checking=True
        self.scan_button.setEnabled(False)
        self.connect_button.setEnabled(False)
        self.status.setText('Scanning for devices…' if scan else 'Connecting…')
        self.watch(Job(self.config_now(),scan=scan),self.connection_result)

    def connection_result(self,result):
        self.scan_serial += 1
        self.checking=False
        if self.quitting:
            return
        self.scan_button.setEnabled(not self.is_busy())
        self.connect_button.setEnabled(not self.is_busy())
        if result.get('devices'):
            self.devices={info['id']:info for info in result['devices']}
            self.redraw_devices()
            self.status.setText('Connected — ready for transfer')
            if self.auto.isChecked():
                self.start(automatic=True)
        else:
            self.devices.clear()
            self.redraw_devices()
            self.status.setText('No device found — check USB or Wi-Fi')
            self.status.setToolTip(result.get('error',''))
        if not self.preview:
            try:
                (DATA/'last-visible-scan.json').write_text(json.dumps({
                    'pid':os.getpid(),'mode':self.mode.currentData(),
                    'received':[{k:d.get(k) for k in ('id','kind','name','mode')} for d in result.get('devices',[])],
                    'cards':list(self.cards),'error':result.get('error'),
                    'diagnostics':result.get('diagnostics')},indent=2),encoding='utf-8')
            except OSError:pass

    def redraw_devices(self):
        while self.device_layout.count():
            item=self.device_layout.takeAt(0)
            if item.widget():
                item.widget().hide()
                item.widget().deleteLater()
        self.cards.clear()
        modern_only = bool(self.devices) and all(i.get('kind') == 'iphone' for i in self.devices.values())
        self.connection_note.setText('' if modern_only else
            'Device needs OpenSSH to be installed\nin order to upload music via Wi-Fi.')
        for index,info in enumerate(self.devices.values()):
            if index:
                line=QFrame()
                line.setFixedHeight(1)
                line.setStyleSheet('background:#cccccc;border:0;')
                self.device_layout.addWidget(line)
            card=DeviceCard(info)
            card.eject.connect(self.eject)
            self.cards[info['id']]=card
            self.device_layout.addWidget(card)

    def eject(self,device_id):
        if self.current_device==device_id and self.is_busy():
            self.worker.stop.set()
            self.status.setText('Ejecting — finishing the current operation safely…')
        self.devices.pop(device_id,None)
        self.redraw_devices()
        if not self.is_busy():
            self.status.setText('Device removed from selection')

    def is_busy(self):
        return bool(self.worker and self.worker.isRunning())

    def auto_changed(self,checked):
        self.upload.setEnabled(not checked and not self.is_busy())
        self.config_now()
        if checked and not self.preview:
            self.start(automatic=True)

    def add_paths(self,values):
        existing={str(p).casefold() for p in self.paths}
        for path in discover(values):
            if str(path).casefold() not in existing:
                self.paths.append(path)
                existing.add(str(path).casefold())
                self.states[str(path)]='Queued'
        self.redraw_queue()
        if self.auto.isChecked():
            if self.devices:
                self.start(automatic=True)
            elif not self.preview:
                self.scan()

    def redraw_queue(self):
        self.table.setRowCount(len(self.paths))
        self.rows.clear()
        for row,path in enumerate(self.paths):
            key=str(path)
            self.rows[key]=row
            try:size=human_size(path.stat().st_size)
            except OSError:size='—'
            meta=self.metadata.get(key,{})
            for col,value in enumerate([meta.get('title',path.stem),meta.get('format',path.suffix[1:].upper()),size,self.states.get(key,'Queued')]):
                current=self.table.item(row,col)
                if current is None:
                    current=QTableWidgetItem(value)
                    self.table.setItem(row,col,current)
                else:
                    current.setText(value)
                current.setToolTip(key if col==0 else value)
        self.count.setText(f'{len(self.paths)} tracks selected')

    def start(self,automatic=False):
        if self.preview or self.is_busy() or self.checking:
            return
        pending=[p for p in self.paths if self.states.get(str(p)) not in ('On iPod','Already on iPod','On iPhone','Already on iPhone')
                 and (not automatic or str(p) not in self.attempted)]
        if not pending:
            if not automatic:self.status.setText('0 tracks selected')
            return
        if not self.devices:
            self.status.setText('Connect a device first')
            return
        if len(self.devices) > 1:
            self.status.setText('Eject other devices before uploading')
            return
        info=next(iter(self.devices.values()))
        self.current_device_kind=info.get('kind','touch')
        self.current_device=info['id']
        self.batch_paths=list(pending)
        self.attempted.update(str(p) for p in pending)
        self.worker=Job(info['config'],pending)
        self.upload.setEnabled(False)
        self.mode.setEnabled(False)
        self.ip.setEnabled(False)
        self.scan_button.setEnabled(False)
        self.connect_button.setEnabled(False)
        self.auto.setEnabled(False)
        self.last_progress={'tracks_total':len(pending),'imported':0,'bytes_transferred':0,
                            'bytes_total':sum(p.stat().st_size for p in pending),'bytes_estimated':True}
        self.speed.reset()
        self.progress_timer.start()
        self.show_progress('Preparing transfer',self.last_progress,0)
        self.watch(self.worker,self.transfer_result)

    def show_progress(self,title,event,fraction=None):
        self.last_progress.update(event)
        s=self.last_progress
        done=s.get('imported',0)
        total=s.get('tracks_total',len(self.batch_paths))
        amount=s.get('bytes_transferred',0)/1024**3
        size=s.get('bytes_total',0)/1024**3
        estimate='≈' if s.get('bytes_estimated') else ''
        detail=f'Imported {done} / {total} tracks  ·  {amount:.2f} / {estimate}{size:.2f} GB transferred'
        rate=self.speed.update(s.get('bytes_transferred',0),event.get('stage') in ('Uploading','Загрузка на iPod'))
        detail+=f'  ·  {rate/1024**2:.1f} MB/s'
        self.lcd.display(title,detail,event.get('overall',0) if fraction is None else fraction)

    def refresh_speed(self):
        if self.is_busy() and self.last_progress.get('stage') in ('Uploading','Загрузка на iPod'):
            self.show_progress(self.lcd.title,self.last_progress)

    def on_event(self,event):
        if self.quitting:return
        if event.get('type')=='connection':
            if self.current_device in self.devices:
                self.devices[self.current_device].update(event)
                if 'free' in event and 'total' in event:
                    self.devices[self.current_device]['capacity_known']=True
                self.redraw_devices()
            return
        key=event.get('path')
        state_map={'Подготовка':'Preparing','Конвертация':'Converting','Загрузка':'Uploading',
            'Добавление':'Importing','Проверка':'Verifying','На iPod':'On iPod',
            'Уже на iPod':'Already on iPod','Ошибка':'Failed','Остановлено':'Stopped'}
        if key in self.rows:
            row=self.rows[key]
            meta=self.metadata.setdefault(key,{})
            if event.get('title'):
                self.table.item(row,0).setText(event['title'])
                meta['title']=event['title']
            if event.get('format'):
                self.table.item(row,1).setText(event['format'])
                meta['format']=event['format']
            if event.get('state'):
                state=state_map.get(event['state'],event['state'])
                self.states[key]=state
                item=self.table.item(row,3)
                item.setText(state)
                item.setForeground(QColor('#63816b' if state in ('On iPod','Already on iPod','On iPhone','Already on iPhone') else '#a15c57' if state=='Failed' else '#636363'))
                item.setToolTip(event.get('detail',state))
                self.table.scrollToItem(item)
        stage={'Читаем информацию':'Reading metadata','Подготовка':'Preparing',
            'Конвертация в ALAC':'Converting to ALAC','Загрузка на iPod':'Uploading',
            'Добавляем в «Музыку»':'Importing into Music','Проверяем библиотеку':'Verifying library',
            'Трек добавлен в «Музыку»':'Imported','Трек уже на iPod':'Already on iPod',
            'Перенос остановлен':'Stopped','Не удалось добавить трек':'Import failed',
            'Подготавливаем iPod':'Preparing iPod','Завершаем импорт':'Finishing import'}.get(event.get('stage'),event.get('stage',''))
        title=(event.get('title','')+' — '+stage).strip(' —')
        self.show_progress(title,event)
        self.status.setText(stage)
        self.status.setToolTip(event.get('detail',''))

    def transfer_result(self,result):
        self.progress_timer.stop()
        if self.quitting:return
        self.worker=None
        self.upload.setEnabled(not self.auto.isChecked())
        for item in [self.mode,self.ip,self.scan_button,self.connect_button,self.auto]:item.setEnabled(True)
        if result.get('error'):
            self.status.setText('Transfer failed — check the connection')
            self.status.setToolTip(result['error'])
            self.show_progress('Transfer interrupted',self.last_progress)
        else:
            self.last_progress.update(result)
            count=result.get('imported',0)
            skipped=result.get('skipped',0)
            errors=result.get('errors',0)
            title='Transfer stopped' if result.get('cancelled') else 'Transfer interrupted' if result.get('interrupted') else 'Transfer complete' if not errors else 'Transfer completed with errors'
            self.show_progress(title,self.last_progress,None if result.get('interrupted') else 0 if result.get('cancelled') else 1)
            device='iPhone' if self.current_device_kind=='iphone' else 'iPod'
            self.status.setText(f'Imported: {count} · already on {device}: {skipped} · failed: {errors}')
        if self.auto.isChecked():QTimer.singleShot(100,lambda:self.start(automatic=True))

    def remove_selected(self):
        if self.is_busy():return
        selected={i.row() for i in self.table.selectionModel().selectedRows()}
        self.paths=[p for i,p in enumerate(self.paths) if i not in selected]
        self.redraw_queue()

    def queue_menu(self,point):
        menu=QMenu(self)
        menu.addAction('Remove selected',self.remove_selected).setEnabled(not self.is_busy())
        action=menu.addAction('Clear queue')
        action.setEnabled(not self.is_busy())
        action.triggered.connect(self.clear_queue)
        menu.exec(self.table.viewport().mapToGlobal(point))

    def clear_queue(self):
        if self.is_busy():return
        self.paths.clear()
        self.states.clear()
        self.metadata.clear()
        self.attempted.clear()
        self.redraw_queue()
        self.lcd.display(active=False)
        self.status.setText('Ready for transfer')

    def closeEvent(self,event):
        if self.is_busy() and QMessageBox.question(self,APP_NAME,'Stop the transfer and close?')!=QMessageBox.Yes:
            event.ignore()
            return
        if self.jobs:
            self.quitting=True
            for job in self.jobs:job.stop.set()
            self.hide()
            event.ignore()
        else:event.accept()

    def preview_devices(self):
        self.devices={
            'preview-touch':{'id':'preview-touch','kind':'touch','name':'iPod touch 4','mode':'Wi-Fi',
                             'free':32.9*1024**3,'total':60.7*1024**3},
            'preview-classic':{'id':'preview-classic','kind':'classic','name':'iPod Classic','mode':'USB',
                               'free':14.3*1024**3,'total':15.75*1024**3}}
        self.redraw_devices()


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--render',type=Path)
    parser.add_argument('--check-devices',type=Path)
    parser.add_argument('--diagnose-startup',type=Path)
    parser.add_argument('--preview-state',choices=['ready','empty','uploading','iphone'],default='ready')
    args=parser.parse_args()
    if args.check_devices:
        try:
            # Exercise the same cold worker path as the visible app. Warming
            # native imports in the main thread hid worker-only failures.
            check_app=QCoreApplication(sys.argv[:1])
            received=[]
            job=Job(settings(),scan=True)
            job.result.connect(received.append)
            job.finished.connect(check_app.quit)
            job.start();check_app.exec();job.wait()
            if not received or received[0].get('error'):
                raise RuntimeError(received[0].get('error','Discovery returned no result') if received else 'Discovery returned no result')
            devices=received[0].get('devices',[])
            from modern_music import native_sign
            signer_ok=len(native_sign('request'))==84
            result={'version':VERSION,'signer':signer_ok,'devices':[
                {k:v for k,v in d.items() if k not in ('config','id')} for d in devices]}
        except Exception as error:
            result={'version':VERSION,'error':str(error) or type(error).__name__}
        args.check_devices.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        return
    QApplication.setAttribute(Qt.AA_DontUseNativeDialogs,True)
    app=QApplication(sys.argv[:1])
    app.setApplicationName(APP_NAME)
    apply_light_theme(app)
    load_fonts()
    app.setFont(font(10))
    lock=None
    if not args.render and not args.diagnose_startup:
        DATA.mkdir(parents=True,exist_ok=True)
        lock=QLockFile(str(DATA/'iMetal.lock'))
        if not lock.tryLock(100):
            QMessageBox.information(None,APP_NAME,'The application is already open.')
            return
    window=Window(preview=bool(args.render))
    if args.diagnose_startup:
        # Initialize the real Windows Qt application and its normal startup
        # scan, but never show a desktop window or enable user input.
        rounds=[]
        observer=QTimer()
        observer.setInterval(100)
        def observe():
            if window.checking or window.scan_serial<=len(rounds):return
            rounds.append({'devices':[{k:d.get(k) for k in ('kind','name','mode')} for d in window.devices.values()],
                           'cards':list(window.cards),'pictures_loaded':all(not c.picture.isNull() for c in window.cards.values())})
            if len(rounds)==1:
                QTimer.singleShot(100,window.scan)
            else:
                args.diagnose_startup.write_text(json.dumps({'version':VERSION,'rounds':rounds},indent=2),encoding='utf-8')
                window.grab().save(str(args.diagnose_startup.with_suffix('.png')))
                observer.stop();app.quit()
        observer.timeout.connect(observe);observer.start()
        app.exec()
        return
    if args.render:
        window.auto.setChecked(True)
        window.mode.setCurrentIndex(0)
        if args.preview_state not in ('empty','iphone'):window.preview_devices()
        if args.preview_state=='iphone':
            window.devices={'preview-phone':{'id':'preview-phone','kind':'iphone','name':'iPhone 16 Pro',
                'mode':'Wi-Fi','free':59*1024**3,'total':256*1024**3}}
            window.redraw_devices()
        if args.preview_state=='uploading':
            window.show_progress('C418 — plastik · Uploading',{'tracks_total':12,'imported':4,
                'bytes_transferred':1.25*1024**3,'bytes_total':3.7*1024**3,'bytes_estimated':False},.42)
    window.show()
    if args.render:
        app.processEvents()
        args.render.parent.mkdir(parents=True,exist_ok=True)
        window.grab().save(str(args.render))
        return
    sys.exit(app.exec())
