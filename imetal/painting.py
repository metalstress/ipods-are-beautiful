"""DPI-aware antialiased compositing for native QWidget surfaces."""
from PIL import Image, ImageChops, ImageFilter, ImageOps
from PySide6.QtCore import Qt, QRectF, QPointF, QPoint
from PySide6.QtGui import QImage, QPainter, QColor, QPixmap
from PySide6.QtWidgets import QGraphicsEffect


def alpha_shape(width,height,rect,radius):
    image=QImage(width,height,QImage.Format_ARGB32_Premultiplied)
    image.fill(Qt.transparent)
    painter=QPainter(image)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setPen(Qt.NoPen)
    painter.setBrush(Qt.white)
    painter.drawRoundedRect(rect,radius,radius)
    painter.end()
    alpha=image.convertToFormat(QImage.Format_Alpha8)
    return Image.frombytes('L',(width,height),alpha.constBits().tobytes(),'raw','L',alpha.bytesPerLine(),1)


def inner_shadow(width,height,dpr,radius=12,blur=2.7,spread=1,offset=(0,2),opacity=.3,color=(0,0,0)):
    """CSS/Figma inset shadow: clip × inverse(blurred, inset, offset shape)."""
    w,h=max(1,round(width*dpr)),max(1,round(height*dpr))
    pad=max(4,round((blur*3+spread+max(abs(v) for v in offset))*dpr))
    shape=QRectF(0,0,w,h)
    outer=alpha_shape(w,h,shape,radius*dpr)
    cutout=shape.adjusted(spread*dpr,spread*dpr,-spread*dpr,-spread*dpr)
    cutout.translate(pad+offset[0]*dpr,pad+offset[1]*dpr)
    cutout_alpha=alpha_shape(w+2*pad,h+2*pad,cutout,max(0,radius-spread)*dpr)
    cutout_alpha=cutout_alpha.filter(ImageFilter.GaussianBlur(blur*dpr/2))
    cutout_alpha=cutout_alpha.crop((pad,pad,pad+w,pad+h))
    alpha=ImageChops.multiply(outer,ImageOps.invert(cutout_alpha)).point(lambda a:round(a*opacity))
    result=Image.new('RGBA',(w,h),(*color,0))
    result.putalpha(alpha)
    image=QImage(result.tobytes(),w,h,w*4,QImage.Format_RGBA8888).copy()
    image.setDevicePixelRatio(dpr)
    return image


class RoundedClip(QGraphicsEffect):
    """Alpha clip after children are drawn; QRegion would produce jagged edges."""
    def __init__(self,radius,parent=None):
        super().__init__(parent)
        self.radius=radius
        self._mask=None
        self._key=None

    def draw(self,painter):
        offset=QPoint()
        source=self.sourcePixmap(Qt.LogicalCoordinates,offset,QGraphicsEffect.NoPad)
        if source.isNull():return
        dpr=source.devicePixelRatio()
        key=(source.width(),source.height(),dpr)
        if key!=self._key:
            mask=QPixmap(source.size())
            mask.setDevicePixelRatio(dpr)
            mask.fill(Qt.transparent)
            p=QPainter(mask)
            p.setRenderHint(QPainter.Antialiasing)
            p.setPen(Qt.NoPen)
            p.setBrush(Qt.white)
            size=source.deviceIndependentSize()
            p.drawRoundedRect(QRectF(0,0,size.width(),size.height()),self.radius,self.radius)
            p.end()
            self._mask=mask
            self._key=key
        output=source.copy()
        p=QPainter(output)
        p.setCompositionMode(QPainter.CompositionMode_DestinationIn)
        p.drawPixmap(QPointF(0,0),self._mask)
        p.end()
        painter.drawPixmap(offset,output)
