"""Native controls measured from the approved iOS 6 library designs."""
from pathlib import Path
import math

from PySide6.QtCore import Qt, QRectF, QSize, Signal, QVariantAnimation, QEasingCurve
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QPixmap, QImage, QLinearGradient
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (QWidget, QPushButton, QHBoxLayout, QVBoxLayout,
    QLabel, QLineEdit, QGraphicsDropShadowEffect)

from ui import font, gradient, layout, ScrollBar
from transport import RESOURCES

ASSETS = RESOURCES/'library'


def text(value, size=12, bold=False, color='#ebebeb'):
    w = QLabel(value)
    w.setFont(font(size, bold, 'Helvetica Neue'))
    w.setStyleSheet(f'color:{color};background:transparent;border:0;')
    return w


def image(renderer, painter, rect):
    if renderer.isValid():
        renderer.render(painter, QRectF(rect))


class BarButton(QPushButton):
    def __init__(self, title, red=False, parent=None):
        super().__init__(title, parent)
        self.red = red
        self.setFont(font(12, True, 'Helvetica Neue'))
        self.setFixedHeight(29)
        self.setMinimumWidth(max(52, self.fontMetrics().horizontalAdvance(title)+20))
        self.setCursor(Qt.PointingHandCursor)
        self.setAccessibleName(title)

    def sizeHint(self):
        return QSize(max(52, self.fontMetrics().horizontalAdvance(self.text())+20), 29)

    def paintEvent(self, event):
        p = QPainter(self);p.setRenderHint(QPainter.Antialiasing)
        if not self.isEnabled():
            p.setOpacity(.4)
        r = QRectF(.5, .5, self.width()-1, self.height()-1.5)
        p.setPen(QPen(QColor(255,255,255,100), .5))
        p.setBrush(Qt.NoBrush);p.drawRoundedRect(r.translated(0,.5), 5,5)
        if self.red:
            stops = [(0,'#db8b92'),(.33,'#c3626a'),(.67,'#b2303b'),(1,'#b01a27')]
        else:
            stops = [(0,'#808080'),(.519,'#1a1a1a'),(.52,'#000000'),(1,'#0d0d0d')]
        if self.isDown():
            stops = [(0,'#111111'),(1,'#444444')] if not self.red else [(0,'#8c1823'),(1,'#ae414c')]
        p.setBrush(gradient(r,stops));p.setPen(QPen(QColor(0,0,0,160),.7));p.drawRoundedRect(r,5,5)
        p.setFont(self.font());p.setPen(QColor(0,0,0,150))
        p.drawText(self.rect().translated(0,-1),Qt.AlignCenter,self.text())
        p.setPen(QColor('white'));p.drawText(self.rect(),Qt.AlignCenter,self.text())
        if self.hasFocus():
            p.setBrush(Qt.NoBrush);p.setPen(QPen(QColor('#cccccc'),.5,Qt.DotLine))
            p.drawRoundedRect(r.adjusted(3,3,-3,-3),2,2)


class LibraryToolbar(QWidget):
    def __init__(self):
        super().__init__();self.setFixedHeight(44)
        self.title = ''
        self.left = None;self.right = []

    def configure(self, title, left, right):
        for b in ([self.left] if self.left else [])+self.right:
            b.hide();b.deleteLater()
        self.title = title;self.left = None;self.right = []
        if left:
            self.left = self.button(left)
        for spec in right:
            self.right.append(self.button(spec))
        self.arrange();self.update()

    def button(self, spec):
        caption, callback, *options = spec
        b = BarButton(caption, bool(options and options[0]), self)
        if len(options)>1:
            b.setEnabled(options[1])
        b.clicked.connect(callback);b.show()
        return b

    def arrange(self):
        if self.left:
            self.left.setGeometry(8,8,self.left.sizeHint().width(),29)
        x = self.width()-8
        for b in reversed(self.right):
            w = b.sizeHint().width();x-=w;b.setGeometry(x,8,w,29);x-=10

    def resizeEvent(self, event):
        self.arrange()

    def paintEvent(self, event):
        p=QPainter(self);r=QRectF(self.rect())
        p.fillRect(r,QColor('#1c1c1c'))
        p.fillRect(r,gradient(r,[(0,'#5b5b5b'),(.519,'#292929'),(.52,'#1c1c1c'),(1,'#1c1c1c')]))
        p.setPen(QPen(QColor(255,255,255,50),1));p.drawLine(0,1,self.width(),1)
        left=self.left.width()+18 if self.left else 8
        right=sum(b.width()+10 for b in self.right)+8
        margin=max(left,right)
        title=QRectF(margin,0,max(0,self.width()-2*margin),44)
        p.setFont(font(16,True,'Helvetica Neue'))
        value=p.fontMetrics().elidedText(self.title,Qt.ElideRight,int(title.width()))
        p.setPen(QColor(0,0,0,160));p.drawText(title.translated(0,-1),Qt.AlignCenter,value)
        p.setPen(QColor('white'));p.drawText(title,Qt.AlignCenter,value)


class TabButton(QPushButton):
    def __init__(self, title, key):
        super().__init__();self.title=title;self.key=key;self.active=False
        self.renderer=QSvgRenderer(str(ASSETS/(key+'.svg')))
        self.setFixedHeight(49);self.setCursor(Qt.PointingHandCursor);self.setAccessibleName(title)

    def paintEvent(self,event):
        p=QPainter(self)
        stops=[(0,'#b7b6b6'),(.33,'#c9c9c9'),(.67,'#dddedf'),(1,'#d9dada')] if self.active else [
            (0,'#f2f4f4'),(.33,'#e1e4e6'),(.67,'#d9dede'),(1,'#dedfe0')]
        p.fillRect(self.rect(),gradient(QRectF(self.rect()),stops))
        p.setPen(QColor(255,255,255,150));p.drawLine(0,0,self.width(),0)
        p.setPen(QColor(0,0,0,64));p.drawLine(self.width()-1,0,self.width()-1,49)
        icon=QImage(40,30,QImage.Format_ARGB32_Premultiplied);icon.fill(Qt.transparent)
        ip=QPainter(icon);ip.fillRect(icon.rect(),gradient(QRectF(icon.rect()),[(0,'#5a5c5d'),(1,'#b4b7b9')]))
        mask=QImage(40,30,QImage.Format_ARGB32_Premultiplied);mask.fill(Qt.transparent)
        mp=QPainter(mask);self.renderer.render(mp,QRectF(0,0,40,30));mp.end()
        ip.setCompositionMode(QPainter.CompositionMode_DestinationIn);ip.drawImage(0,0,mask);ip.end()
        p.drawImage((self.width()-40)//2,4,icon)
        p.setFont(font(10,True,'Helvetica Neue'));p.setPen(QColor('#474747'))
        p.drawText(QRectF(0,33,self.width(),16),Qt.AlignCenter,self.title)


class TabBar(QWidget):
    changed=Signal(str)
    def __init__(self):
        super().__init__();self.setFixedHeight(49)
        row=layout(self,False);self.buttons={}
        for title,key in [('Playlists','playlists'),('Artists','artists'),('Songs','tracks'),('Albums','albums')]:
            b=TabButton(title,'songs' if key=='tracks' else key);b.clicked.connect(lambda checked=False,k=key:self.changed.emit(k))
            row.addWidget(b,1);self.buttons[key]=b

    def select(self,key):
        for name,b in self.buttons.items():b.active=name==key;b.update()


class TabTransition(QWidget):
    def __init__(self, parent):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.snapshot = QPixmap()
        self.direction = 1
        self.animation = QVariantAnimation(self)
        self.animation.setStartValue(0.0)
        self.animation.setEndValue(1.0)
        self.animation.setDuration(185)
        self.animation.setEasingCurve(QEasingCurve.OutCubic)
        self.animation.valueChanged.connect(lambda _: self.update())
        self.animation.finished.connect(self.finish)
        self.hide()

    def begin(self, snapshot, direction):
        self.animation.stop()
        self.snapshot = snapshot
        self.direction = direction
        self.setGeometry(self.parentWidget().rect())
        self.show()
        self.raise_()
        self.animation.start()

    def finish(self):
        self.animation.stop()
        self.hide()
        self.snapshot = QPixmap()

    def paintEvent(self, event):
        progress = self.animation.currentValue()
        painter = QPainter(self)
        painter.setOpacity(1.0 - progress)
        painter.drawPixmap(round(-8 * progress * self.direction), 0, self.snapshot)


class LibraryScrollBar(ScrollBar):
    def mouseMoveEvent(self,event):
        if self._drag is not None:
            start_y,start_value=self._drag
            travel=max(1,self.height()-12-self.thumb_rect().height())
            self.setValue(round(start_value+(event.position().y()-start_y)*(self.maximum()-self.minimum())/travel))
            event.accept()
        else:super().mouseMoveEvent(event)

    def thumb_rect(self):
        travel=max(0,self.height()-12)
        if self.maximum()<=self.minimum():return QRectF()
        h=max(24,travel*self.pageStep()/max(1,self.maximum()-self.minimum()+self.pageStep()))
        y=6+(travel-h)*(self.value()-self.minimum())/(self.maximum()-self.minimum())
        return QRectF(3.5,y,5,h)

    def paintEvent(self,event):
        p=QPainter(self);p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen);p.setBrush(QColor(235,235,235,166));p.drawRoundedRect(self.thumb_rect(),2.5,2.5)


class DarkDialog(QWidget):
    def __init__(self, width=360, parent=None, gloss=True):
        super().__init__(parent);self.setFixedWidth(width);self.gloss=gloss
        self.renderer=QSvgRenderer(str(ASSETS/('dialog-gloss.svg' if width==443 else 'dialog-gloss-small.svg')))
        self.setAttribute(Qt.WA_StyledBackground,False)
        self.body=layout(self,margins=(16,14,16,14),spacing=14)
        effect=QGraphicsDropShadowEffect(self);effect.setOffset(0,4);effect.setBlurRadius(10);effect.setColor(QColor(0,0,0,128));self.setGraphicsEffect(effect)

    def paintEvent(self,event):
        p=QPainter(self);p.setRenderHint(QPainter.Antialiasing)
        r=QRectF(self.rect()).adjusted(1,1,-1,-1)
        p.setBrush(QColor(11,11,11,204));p.setPen(QPen(QColor(255,255,255,178),2));p.drawRoundedRect(r,10,10)
        path=QPainterPath();path.addRoundedRect(r,10,10);p.setClipPath(path)
        if self.gloss:image(self.renderer,p,QRectF(0,0,self.width(),29))


def input_field(value='', placeholder=''):
    w=QLineEdit(value);w.setPlaceholderText(placeholder);w.setFont(font(16,False,'Helvetica Neue'))
    w.setFixedHeight(34)
    w.setStyleSheet('QLineEdit {font-family:"Helvetica Neue";font-size:16px;color:#b0b0b0;'
        'background:white;border:1px solid black;border-radius:7px;padding:2px 4px;}'
        'QLineEdit:focus {color:#333333;} QLineEdit:disabled{background:#ededed;}')
    return w


class PlaylistWheel(QWidget):
    selected=Signal(int)
    def __init__(self, playlists, parent=None):
        super().__init__(parent);self.playlists=playlists;self.position=0.;self.index=0
        self.setFixedSize(176,161);self.setFocusPolicy(Qt.StrongFocus);self.press=None
        self.animation=QVariantAnimation(self);self.animation.setDuration(150)
        self.animation.setEasingCurve(QEasingCurve.OutCubic)
        self.animation.valueChanged.connect(self.set_position)

    def set_position(self,value):self.position=float(value);self.update()

    def choose(self,index,animate=True):
        if not self.playlists:return
        self.index=max(0,min(len(self.playlists)-1,index));self.selected.emit(self.index)
        self.animation.stop()
        if animate:
            self.animation.setStartValue(self.position);self.animation.setEndValue(float(self.index));self.animation.start()
        else:self.set_position(self.index)

    def wheelEvent(self,event):
        self.choose(self.index-(1 if event.angleDelta().y()>0 else -1));event.accept()

    def keyPressEvent(self,event):
        if event.key() in (Qt.Key_Up,Qt.Key_Down):self.choose(self.index+(-1 if event.key()==Qt.Key_Up else 1))
        else:super().keyPressEvent(event)

    def mousePressEvent(self,event):
        self.animation.stop();self.press=(event.position().y(),self.position)

    def mouseMoveEvent(self,event):
        if self.press and self.playlists:
            self.set_position(max(0,min(len(self.playlists)-1,self.press[1]-(event.position().y()-self.press[0])/44)))

    def mouseReleaseEvent(self,event):
        if self.press:
            delta=event.position().y()-self.press[0]
            self.choose(round(self.position) if abs(delta)>4 else round(self.position+(event.position().y()-81)/44))
        self.press=None

    def paintEvent(self,event):
        p=QPainter(self);p.setRenderHint(QPainter.Antialiasing)
        path=QPainterPath();path.addRoundedRect(QRectF(self.rect()),5,5);p.setClipPath(path)
        p.fillRect(self.rect(),QColor('#fcfcfc'))
        p.setFont(font(16,True,'Helvetica Neue'));p.setPen(QColor('black'))
        for i,row in enumerate(self.playlists):
            y=59+(i-self.position)*44
            if -44<y<161:
                value=p.fontMetrics().elidedText(row['name'],Qt.ElideRight,146)
                p.drawText(QRectF(15,y,146,44),Qt.AlignCenter,value)
        if not self.playlists:
            p.setFont(font(13,False,'Helvetica Neue'));p.setPen(QColor('#636363'));p.drawText(self.rect(),Qt.AlignCenter,'No playlists')
        else:
            r=QRectF(0,59,176,44)
            p.fillRect(r,gradient(r,[(0,'#dbdbdb'),(.5,'#b6b6b6'),(.501,'#969696'),(1,'#c0c0c0')]))
            p.setFont(font(16,True,'Helvetica Neue'));p.setPen(QColor('#3b3b3b'))
            for i,row in enumerate(self.playlists):
                y=59+(i-self.position)*44
                p.save();p.setClipRect(r,Qt.IntersectClip)
                p.drawText(QRectF(15,y,146,44),Qt.AlignCenter,p.fontMetrics().elidedText(row['name'],Qt.ElideRight,146));p.restore()
        for r,stops in [(QRectF(0,0,176,59),[(0,'#99000000'),(1,'#00000000')]),
                        (QRectF(0,103,176,58),[(0,'#00000000'),(1,'#99000000')])]:
            p.fillRect(r,gradient(r,stops))
