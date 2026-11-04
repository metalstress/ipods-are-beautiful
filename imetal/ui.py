"""Native widgets measured from Figma 16:10428, not a rasterized interface."""
from pathlib import Path
import os

from PySide6.QtCore import Qt, QRectF, QPointF, Signal, QSize, QTimer
from PySide6.QtGui import QColor, QFont, QFontDatabase, QPainter, QPainterPath, QPen, QLinearGradient, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (QWidget, QFrame, QLabel, QPushButton, QAbstractButton,
    QHBoxLayout, QVBoxLayout, QFileDialog, QCheckBox, QSizeGrip, QTableWidget,
    QHeaderView, QAbstractItemView, QScrollBar)

from transport import RESOURCES
from painting import inner_shadow,RoundedClip
from version import APP_NAME

ASSETS = RESOURCES / 'design-v2'
COLORS = dict(window='#d6d6d6', sidebar='#ededed', chrome='#e4e4e4',
    footer='#dcdcdc', border='#afafaf', text='#636363', muted='#848484',
    field='#484848', placeholder='#afafaf', heading='#6a7078', subtitle='#808895')


def font(size=10, bold=False, family='Tahoma'):
    value = QFont(family)
    value.setPixelSize(size)
    value.setBold(bold)
    return value


def load_fonts():
    folders = [Path(os.environ.get('WINDIR', r'C:\Windows')) / 'Fonts',
               Path(os.environ.get('LOCALAPPDATA', Path.home()/'AppData/Local')) / 'Microsoft/Windows/Fonts']
    for folder in folders:
        for name in ['tahoma.ttf', 'tahomabd.ttf', 'seguisym.ttf',
                     'HelveticaNeueBold.otf', 'HelveticaNeueMedium.otf', 'HelveticaNeueRoman.otf']:
            if (folder / name).exists():
                QFontDatabase.addApplicationFont(str(folder / name))


def label(value, size=10, color=None, bold=False, height=12, parent=None):
    item = QLabel(value, parent)
    item.setFont(font(size, bold))
    item.setStyleSheet('color:' + (color or COLORS['text']) + ';background:transparent;border:0;')
    item.setFixedHeight(height)
    return item


def layout(widget, vertical=True, margins=(0, 0, 0, 0), spacing=0):
    box = QVBoxLayout(widget) if vertical else QHBoxLayout(widget)
    box.setContentsMargins(*margins)
    box.setSpacing(spacing)
    return box


def gradient(rect, stops):
    value = QLinearGradient(rect.left(), rect.top(), rect.left(), rect.bottom())
    for at, color in stops:
        value.setColorAt(at, QColor(color))
    return value


def separator_fill(painter,rect):
    fade=QLinearGradient(rect.left(),0,rect.right(),0)
    fade.setColorAt(0,QColor(204,204,204,0))
    fade.setColorAt(.5,QColor('#cccccc'))
    fade.setColorAt(1,QColor(204,204,204,0))
    painter.fillRect(rect,fade)


class ConnectionForm(QFrame):
    def __init__(self):
        super().__init__()
        self.separator_marker=None

    def paintEvent(self,event):
        super().paintEvent(event)
        if self.separator_marker is not None:
            painter=QPainter(self)
            point=self.separator_marker.pos()
            separator_fill(painter,QRectF(point.x(),point.y(),200,1))


class Footer(QFrame):
    def paintEvent(self,event):
        painter=QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor('#dcdcdc'))
        painter.drawRoundedRect(QRectF(0,-14,self.width(),self.height()+14),14,14)
        painter.setPen(QPen(QColor('#989898'),.5))
        painter.drawLine(QPointF(0,.25),QPointF(self.width(),.25))


class Svg(QWidget):
    def __init__(self, name, width, height, parent=None):
        super().__init__(parent)
        self.renderer = QSvgRenderer(str(ASSETS / (name + '.svg')))
        self.setFixedSize(width, height)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        self.renderer.render(painter, QRectF(self.rect()))


class SilverButton(QPushButton):
    def __init__(self, title, parent=None):
        super().__init__(title, parent)
        self.setFont(font(11))
        self.setFixedHeight(25)
        self.setCursor(Qt.PointingHandCursor)
        self.setMouseTracking(True)
        self.setAccessibleName(title)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        if not self.isEnabled():
            painter.setOpacity(.3)
        rect = QRectF(self.rect()).adjusted(.5, .5, -.5, -.5)
        stops = [(0, '#ffffff'), (.46, '#f0f0f0'), (.5, '#e0e0e0'), (1, '#ffffff')]
        if self.isDown():
            stops = [(0, '#c6c6c6'), (1, '#e3e3e3')]
        elif self.underMouse() and self.isEnabled():
            stops = [(0, '#ffffff'), (.46, '#f4f4f4'), (.5, '#e6e6e6'), (1, '#ffffff')]
        painter.setBrush(gradient(rect, stops))
        painter.setPen(QPen(QColor('#acacac'), 1))
        painter.drawRoundedRect(rect, 5, 5)
        painter.setPen(QColor(COLORS['text']))
        painter.setFont(self.font())
        painter.drawText(self.rect(), Qt.AlignCenter, self.text())
        if self.hasFocus():
            painter.setBrush(Qt.NoBrush)
            painter.setPen(QPen(QColor('#777777'), .5, Qt.DotLine))
            painter.drawRoundedRect(rect.adjusted(3, 3, -3, -3), 2, 2)


class TrafficLight(QAbstractButton):
    def __init__(self, color, tip, callback, parent=None):
        super().__init__(parent)
        self.color = color
        self.setFixedSize(12, 12)
        self.setToolTip(tip)
        self.setAccessibleName(tip)
        self.clicked.connect(callback)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(QPen(QColor('#7e8584'), 1))
        painter.setBrush(QColor(self.color))
        painter.drawEllipse(QRectF(.5, .5, 11, 11))


class TitleBar(QFrame):
    def __init__(self, owner):
        super().__init__()
        self.owner = owner
        self.setFixedHeight(27)
        row = layout(self, False, (10, 0, 10, 0))
        controls = QWidget()
        controls.setFixedSize(50, 12)
        lights = layout(controls, False, spacing=6)
        for color, name, callback in [('#df6f67', 'Close', owner.close),
            ('#e3bf57', 'Minimize', owner.showMinimized),
            ('#84bb75', 'Zoom', owner.toggle_maximized)]:
            lights.addWidget(TrafficLight(color, name, callback))
        row.addWidget(controls)
        title = label(APP_NAME, color='#626e76', bold=True)
        title.setAlignment(Qt.AlignCenter)
        row.addWidget(title, 1)
        balance = QWidget()
        balance.setFixedWidth(50)
        row.addWidget(balance)

    def paintEvent(self,event):
        painter=QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor('#e4e4e4'))
        # Only the upper corners belong to the window outline.
        painter.drawRoundedRect(QRectF(0,0,self.width(),self.height()+14),14,14)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and self.owner.windowHandle():
            self.owner.windowHandle().startSystemMove()

    def mouseDoubleClickEvent(self, event):
        self.owner.toggle_maximized()


class LCD(QWidget):
    """Idle logo, or native text + queue counts + transfer-byte progress."""
    def __init__(self):
        super().__init__()
        self.setFixedHeight(69)
        self.logo = QSvgRenderer(str(ASSETS / 'logo.svg'))
        self.active = False
        self.title = ''
        self.detail = ''
        self.fraction = 0
        self._shadow_key=None
        self._shadow=None

    def display(self, title='', detail='', fraction=0, active=True):
        self.title, self.detail = title, detail
        self.fraction = max(0, min(1, fraction))
        self.active = active
        self.setToolTip(title + ('\n' + detail if detail else ''))
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        box = QRectF(self.rect()).adjusted(.25, .25, -.25, -.25)
        surface=QRectF(self.rect())
        p.setBrush(gradient(surface, [(0, '#edf0d8'), (.08, '#e0e6c7'), (1, '#cbd4b0')]))
        p.setPen(Qt.NoPen)
        p.drawRoundedRect(surface,12,12)
        key=(self.width(),self.height(),self.devicePixelRatioF())
        if key!=self._shadow_key:
            self._shadow=inner_shadow(*key)
            self._shadow_key=key
        p.drawImage(QPointF(0,0),self._shadow)
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QColor('#909a7f'), .5))
        p.drawRoundedRect(box,12,12)
        if not self.active:
            self.logo.render(p, QRectF((self.width()-35)/2, 15.8682, 35, 38))
            return
        p.setPen(QColor('#4f4d47'))
        p.setFont(font(12, True))
        title = p.fontMetrics().elidedText(self.title, Qt.ElideRight, self.width()-36)
        p.drawText(QRectF(18, 8, self.width()-36, 17), Qt.AlignCenter, title)
        p.setFont(font(10))
        detail = p.fontMetrics().elidedText(self.detail, Qt.ElideRight, self.width()-36)
        p.drawText(QRectF(18, 29, self.width()-36, 12), Qt.AlignCenter, detail)
        bar = QRectF(18, 50, self.width()-36, 9)
        p.setPen(Qt.NoPen)
        p.setBrush(gradient(bar, [(0, '#afafaf'), (1, '#cecece')]))
        p.drawRoundedRect(bar, 3, 3)
        if self.fraction:
            fill = bar.adjusted(.5, .5, -.5, -.5)
            fill.setWidth(fill.width()*self.fraction)
            p.setPen(Qt.NoPen)
            p.setBrush(gradient(fill, [(.5, '#ededed'), (1, '#cecece')]))
            p.drawRoundedRect(fill, 2, 2)
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QColor('#9e9e9e'), .5))
        p.drawRoundedRect(bar.adjusted(.25,.25,-.25,-.25),2.75,2.75)


class DropZone(QFrame):
    paths = Signal(list)

    def __init__(self):
        super().__init__()
        self.setAcceptDrops(True)
        self.dragging = False
        self.setFixedHeight(184)
        outer = layout(self, margins=(20, 14, 20, 13))
        outer.addStretch()
        group = QWidget()
        group.setFixedHeight(71)
        body = layout(group, spacing=0)
        self.title = label('Drop here', 17, COLORS['heading'], True, 17)
        self.title.setFont(font(17, True, 'Helvetica Neue'))
        self.title.setAlignment(Qt.AlignCenter)
        body.addWidget(self.title)
        body.addSpacing(5)
        subtitle = label('Albums, folders or songs', 12, COLORS['subtitle'], height=12)
        subtitle_font = font(12, False, 'Helvetica Neue')
        subtitle_font.setStyleName('Medium')
        subtitle.setFont(subtitle_font)
        subtitle.setAlignment(Qt.AlignCenter)
        body.addWidget(subtitle)
        body.addSpacing(12)
        self.choose = SilverButton('Select from folder')
        self.choose.setFixedWidth(113)
        self.choose.clicked.connect(self.choose_folder)
        body.addWidget(self.choose, 0, Qt.AlignHCenter)
        outer.addWidget(group)
        outer.addStretch()

    def choose_folder(self):
        path = QFileDialog.getExistingDirectory(self, 'Select music folder', str(Path.home() / 'Music'))
        if path:
            self.paths.emit([path])

    def choose_files(self):
        paths, _ = QFileDialog.getOpenFileNames(self, 'Select music', str(Path.home() / 'Music'),
            'Music (*.mp3 *.m4a *.flac *.aac *.wav *.aif *.aiff *.ogg *.opus *.wma *.ape *.mpc *.caf);;All files (*)')
        if paths:
            self.paths.emit(paths)

    def dragEnterEvent(self, event):
        if self.isEnabled() and event.mimeData().hasUrls() and any(u.isLocalFile() for u in event.mimeData().urls()):
            self.dragging = True
            self.update()
            event.acceptProposedAction()

    def dragLeaveEvent(self, event):
        self.dragging = False
        self.update()

    def dropEvent(self, event):
        self.dragging = False
        self.update()
        self.paths.emit([u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()])
        event.acceptProposedAction()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect()).adjusted(.75, .75, -.75, -.75)
        painter.setBrush(QColor('#e5e5e5') if self.dragging else Qt.NoBrush)
        pen = QPen(QColor('#898989' if self.dragging else '#afafaf'), 1.5)
        pen.setDashPattern([16/1.5, 8/1.5])
        painter.setPen(pen)
        painter.drawRoundedRect(rect, 8, 8)


class CapacityBar(QWidget):
    def __init__(self):
        super().__init__()
        self.setFixedHeight(6)
        self.fraction = 0
        self.track_fill=QPixmap(str(ASSETS/'capacity-track.png'))
        self.used_fill=QPixmap(str(ASSETS/'capacity-used.png'))
        self._track_shadow_key=None
        self._used_shadow_key=None

    def set_capacity(self, free, total):
        # The light face is used storage; the exposed grey track is free.
        self.fraction = max(0,min(1,(total-free)/total)) if total>0 else 0
        self.setToolTip(f'{free/1024**3:.1f} GB free of {total/1024**3:.1f} GB')
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        box = QRectF(0,0,self.width(),6)
        outline=QPainterPath()
        outline.addRoundedRect(box,2,2)
        p.setClipPath(outline)
        p.drawPixmap(box,self.track_fill,QRectF(self.track_fill.rect()))
        dpr=self.devicePixelRatioF()
        if self.fraction:
            fill = QRectF(.5, .5, (self.width()-1)*self.fraction, 5)
            face=QPainterPath()
            face.addRoundedRect(fill,1,1)
            p.save()
            p.setClipPath(face,Qt.IntersectClip)
            p.drawPixmap(fill,self.used_fill,QRectF(self.used_fill.rect()))
            key=(fill.width(),dpr)
            if key!=self._used_shadow_key:
                self._used_shadow=inner_shadow(fill.width(),5,dpr,radius=1,blur=7.2,
                    spread=0,offset=(-1,1),opacity=.25,color=(255,255,255))
                self._used_shadow_key=key
            p.drawImage(fill.topLeft(),self._used_shadow)
            p.restore()
        key=(self.width(),dpr)
        if key!=self._track_shadow_key:
            self._track_shadow=inner_shadow(self.width(),6,dpr,radius=2,blur=1,
                spread=0,offset=(0,.25),opacity=.25)
            self._track_shadow_key=key
        p.drawImage(QPointF(0,0),self._track_shadow)
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QColor('#9e9e9e'),.5))
        p.drawRoundedRect(box.adjusted(.25,.25,-.25,-.25),1.75,1.75)


class EjectButton(QAbstractButton):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.renderer = QSvgRenderer(str(ASSETS / 'eject.svg'))
        self.setFixedSize(9, 9)
        self.setToolTip('Eject — remove device from selection')
        self.setAccessibleName('Eject device')
        self.setCursor(Qt.PointingHandCursor)

    def paintEvent(self, event):
        p = QPainter(self)
        if not self.isEnabled():
            p.setOpacity(.3)
        self.renderer.render(p, QRectF(self.rect()))


class DeviceCard(QWidget):
    eject = Signal(str)

    def __init__(self, info):
        super().__init__()
        self.info = info
        self.setFixedSize(200, 78)
        asset = {'classic':'classic.png','iphone':'iphone16pro-home.jpg'}.get(info.get('kind'),'touch.png')
        self.picture = QPixmap(str(ASSETS / asset))
        row = layout(self, False, (6, 6, 6, 6), 8)
        photo = QWidget()
        photo.setFixedSize(38, 66)
        self.photo = photo
        row.addWidget(photo)
        details = QWidget()
        details.setFixedHeight(62)
        body = layout(details, spacing=8)
        self.name = label('●  ' + info['name'], color='#484848', bold=True)
        self.name.setTextFormat(Qt.RichText)
        safe_name = info['name'].replace('&', '&amp;').replace('<', '&lt;')
        self.name.setText('<span style="color:#39a451">●</span>&nbsp; ' + safe_name)
        body.addWidget(self.name)
        meta = QWidget()
        meta.setFixedHeight(28)
        stack = layout(meta, spacing=4)
        self.connection = label('via ' + info['mode'])
        self.capacity = label(f"{info['free']/1024**3:.1f} GB free" if info.get('capacity_known',True) else '—')
        stack.addWidget(self.connection)
        stack.addWidget(self.capacity)
        body.addWidget(meta)
        self.bar = CapacityBar()
        self.bar.set_capacity(info['free'], info.get('total', info['free']))
        if not info.get('capacity_known',True):self.bar.hide()
        body.addWidget(self.bar)
        row.addWidget(details, 1)
        self.eject_button = EjectButton(self)
        self.eject_button.clicked.connect(lambda: self.eject.emit(info['id']))

    def resizeEvent(self, event):
        self.eject_button.move(round(self.width()-14.5), 10)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        if self.info.get('kind') == 'classic':
            target = QRectF(9, 12, 32, 54)
        elif self.info.get('kind') == 'iphone':
            # Original Apple press image: right-hand front-facing 16 Pro.
            # Crop while rendering; keep the downloaded press image intact.
            target=QRectF(10,8,29.7,61)
            clip=QPainterPath()
            clip.addRoundedRect(target,3.9,3.9)
            p.setClipPath(clip)
            p.drawPixmap(target,self.picture,QRectF(2506,150,898,1861))
            return
        else:
            target = QRectF(8.5, 8, 33, 61)
        p.drawPixmap(target, self.picture, QRectF(self.picture.rect()))


class AutoCheck(QCheckBox):
    def __init__(self):
        super().__init__('auto upload')
        self.setFont(font(10))
        self.setFixedHeight(18)
        self.check = QSvgRenderer(str(ASSETS / 'check.svg'))
        self.setChecked(True)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        if not self.isEnabled():
            p.setOpacity(.4)
        box = QRectF(.5, 3, 12, 12)
        p.fillRect(box, QColor('white'))
        p.setPen(QPen(QColor('#afafaf'), 1))
        p.drawRect(box)
        if self.isChecked():
            self.check.render(p, QRectF(2.6, 5.75, 8, 7))
        p.setFont(self.font())
        p.setPen(QColor(COLORS['muted']))
        p.drawText(QRectF(18, 3, self.width()-18, 12), Qt.AlignLeft | Qt.AlignVCenter, self.text())


class Grip(QSizeGrip):
    def __init__(self, parent):
        super().__init__(parent)
        self.setFixedSize(8, 12)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setFont(font(10))
        p.setPen(QColor('#697b89'))
        p.drawText(self.rect(), Qt.AlignCenter, '◢')


class TrackTable(QTableWidget):
    geometry_changed=Signal()
    def __init__(self):
        super().__init__(0, 4)
        self.setFont(font(11))
        self.setHorizontalHeaderLabels(['Track name', 'Format', 'Size', 'Status'])
        self.verticalHeader().hide()
        self.setFrameShape(QFrame.NoFrame)
        self.setShowGrid(False)
        self.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.horizontalHeader().setFixedHeight(30)
        self.verticalHeader().setDefaultSectionSize(35)
        self.setAlternatingRowColors(True)
        self.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setStyleSheet('''
            QTableWidget {background:#ededed;alternate-background-color:#e6e6e6;color:#636363;
                border:0;selection-background-color:#c9c9c9;selection-color:#484848;outline:0;}
            QHeaderView {background:transparent;}
            QHeaderView::section {padding:0;border:0;border-right:0.5px solid #afafaf;
                border-bottom:0.5px solid #afafaf;color:#636363;font-family:Tahoma;font-size:11px;
                background:qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 #f8f8f8,stop:1 #ebebeb);}
            QScrollBar:vertical {width:10px;background:#e4e4e4;border:0;}
            QScrollBar::handle:vertical {background:#bcbcbc;border:1px solid #a5a5a5;border-radius:4px;min-height:22px;}
            QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical {height:0;}
        ''')

    def resizeEvent(self,event):
        super().resizeEvent(event)
        self.geometry_changed.emit()


class ScrollBar(QScrollBar):
    """An unclipped silver thumb, with matching drag/hit-test geometry."""
    def __init__(self,parent=None):
        super().__init__(Qt.Vertical,parent)
        self.setFixedWidth(12)
        self.setFocusPolicy(Qt.NoFocus)
        self._drag=None

    def thumb_rect(self):
        track=max(0,self.height()-14)
        span=self.maximum()-self.minimum()
        length=track if span<=0 else min(track,max(24,track*self.pageStep()/max(1,span+self.pageStep())))
        offset=(track-length)*(self.value()-self.minimum())/max(1,span)
        return QRectF(2,7+offset,8,length)

    def paintEvent(self,event):
        if self.maximum()<=self.minimum():return
        painter=QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        track=QRectF(2,7,8,max(0,self.height()-14))
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor('#e0e0e0'))
        painter.drawRoundedRect(track,4,4)
        thumb=self.thumb_rect().adjusted(.5,.5,-.5,-.5)
        painter.setPen(QPen(QColor('#a5a5a5'),1))
        painter.setBrush(gradient(thumb,[(0,'#d3d3d3'),(.5,'#bebebe'),(1,'#d1d1d1')]))
        painter.drawRoundedRect(thumb,3.5,3.5)

    def mousePressEvent(self,event):
        if event.button()!=Qt.LeftButton:return super().mousePressEvent(event)
        thumb=self.thumb_rect()
        if thumb.contains(event.position()):
            self._drag=(event.position().y(),self.value())
            self.setSliderDown(True)
        else:
            step=-self.pageStep() if event.position().y()<thumb.top() else self.pageStep()
            self.setValue(self.value()+step)
        event.accept()

    def mouseMoveEvent(self,event):
        if self._drag is not None:
            start_y,start_value=self._drag
            travel=max(1,self.height()-14-self.thumb_rect().height())
            self.setValue(round(start_value+(event.position().y()-start_y)*(self.maximum()-self.minimum())/travel))
            event.accept()
        else:super().mouseMoveEvent(event)

    def mouseReleaseEvent(self,event):
        self._drag=None
        self.setSliderDown(False)
        event.accept()


class TableBorder(QWidget):
    def __init__(self,parent):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)

    def paintEvent(self,event):
        p=QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QPen(QColor('#afafaf'),.5))
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(QRectF(self.rect()).adjusted(.25,.25,-.25,-.25),8,8)


class TableFrame(QFrame):
    def __init__(self, table):
        super().__init__()
        self.table = table
        box = layout(self,False,margins=(1,1,1,1))
        box.addWidget(table)
        table.setGraphicsEffect(RoundedClip(7,table))
        self.scrollbar=ScrollBar(self)
        self.scrollbar.hide()
        box.addWidget(self.scrollbar)
        native=table.verticalScrollBar()
        self.scrollbar.valueChanged.connect(native.setValue)
        native.valueChanged.connect(self.scrollbar.setValue)
        native.rangeChanged.connect(self.sync_scrollbar)
        table.geometry_changed.connect(self.sync_scrollbar)
        self.border = TableBorder(self)

    def sync_scrollbar(self,*args):
        native=self.table.verticalScrollBar()
        self.scrollbar.setRange(native.minimum(),native.maximum())
        self.scrollbar.setPageStep(native.pageStep())
        self.scrollbar.setSingleStep(native.singleStep())
        self.scrollbar.setValue(native.value())
        self.scrollbar.setVisible(native.maximum()>native.minimum())

    def resizeEvent(self, event):
        self.border.setGeometry(self.rect())
        self.border.raise_()
        QTimer.singleShot(0,self.sync_scrollbar)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QPen(QColor('#afafaf'), .5))
        p.setBrush(QColor('#ededed'))
        p.drawRoundedRect(QRectF(self.rect()).adjusted(.25,.25,-.25,-.25),8,8)
