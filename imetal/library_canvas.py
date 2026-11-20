"""Virtualized library content: album grid and native painted list rows."""
from bisect import bisect_left, bisect_right

from PySide6.QtCore import QEasingCurve, Qt, QRectF, Signal, QTimer, QVariantAnimation
from PySide6.QtGui import QColor, QFontMetrics, QPainter, QPen, QPainterPath, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QWidget

from ui import font, gradient
from library_widgets import ASSETS, image


ALBUM_TRACK_FILL = '#323232'
ALBUM_NOTCH_HEIGHT = 8
PREFETCH_ALBUM_ROWS = 2


def album_cover_key(album_id):
    return 'album:' + album_id


def duration(value):
    seconds=max(0,int(value or 0)//1000)
    return f'{seconds//60:02d}:{seconds%60:02d}'


class LibraryCanvas(QWidget):
    action=Signal(dict)
    covers_needed=Signal(list)

    def __init__(self):
        super().__init__();self.setFocusPolicy(Qt.StrongFocus);self.setMouseTracking(True)
        self.setAccessibleName('Music library')
        self.rows=[];self.view='albums';self.selecting=False;self.selected=set()
        self.expanded=None;self.playlist_edit=False;self.row_actions=None;self.editing=False
        self.tracks={};self.album_tracks={};self.covers={};self.cover_sources={};self.blocks=[];self.asked=set()
        self.overflow=False;self.focus_index=0;self.hot=None
        self.renderers={name:QSvgRenderer(str(ASSETS/(name+'.svg'))) for name in ('plus','minus','collapse','chevron')}
        self.album_reveals = {}
        self.reveal_start = {}
        self.reveal_animation = QVariantAnimation(self)
        self.reveal_animation.setStartValue(0.0)
        self.reveal_animation.setEndValue(1.0)
        self.reveal_animation.valueChanged.connect(self.advance_reveal)

    def configure(self,view,rows,tracks,album_tracks,**state):
        expanded = state.get('expanded', self.expanded)
        if view != self.view or expanded != self.expanded:
            self.reveal_animation.stop()
            self.album_reveals = {expanded: 1.0} if expanded is not None and view == 'albums' else {}
        self.view=view;self.rows=rows;self.tracks=tracks;self.album_tracks=album_tracks
        for key,value in state.items():setattr(self,key,value)
        self.rebuild()

    def resizeEvent(self,event):self.rebuild()

    def expand_album(self, album_id):
        self.reveal_animation.stop()
        self.reveal_start = dict(self.album_reveals)
        self.expanded = album_id
        changing_album = album_id is not None and any(key != album_id for key in self.reveal_start)
        self.reveal_animation.setDuration(280 if changing_album else 220)
        self.reveal_animation.setEasingCurve(QEasingCurve.InOutCubic if changing_album else QEasingCurve.OutCubic)
        self.reveal_animation.start()

    def advance_reveal(self, progress):
        switching = self.expanded is not None and any(key != self.expanded for key in self.reveal_start)
        closing = min(1.0, progress * 2) if switching else progress
        opening = max(0.0, progress * 2 - 1) if switching else progress
        self.album_reveals = {key: amount * (1 - closing) for key, amount in self.reveal_start.items()
                              if key != self.expanded and closing < 1}
        if self.expanded is not None:
            start = self.reveal_start.get(self.expanded, 0.0)
            self.album_reveals[self.expanded] = start + (1 - start) * opening
        self.rebuild()

    def append_album_tracks(self, blocks, y, x, tracks, amount):
        visible = round((ALBUM_NOTCH_HEIGHT + len(tracks) * 22) * amount)
        if not tracks or not visible:
            return y
        bottom = y + visible
        y += ALBUM_NOTCH_HEIGHT
        numbers = [str(track.get('track_number') or i + 1) for i, track in enumerate(tracks)]
        metrics = QFontMetrics(font(12, False, 'Helvetica Neue'))
        number_width = max(18, max(metrics.horizontalAdvance(number) + 1 for number in numbers))
        for i, (track, number) in enumerate(zip(tracks, numbers)):
            if i and y >= bottom:
                break
            block = {'type':'track','row':track,'y':y,'h':22,'index':i,'inline':True,
                     'number':number,'number_width':number_width}
            if i == 0:
                block['notch_x'] = x
            if y + 22 > bottom:
                block['visible_bottom'] = bottom
            blocks.append(block)
            y += 22
        return bottom

    def rebuild(self):
        blocks=[];y=0
        if self.view=='albums':
            margin, cover_size, min_gap = 12, 118, 12
            usable_width = self.width() - 2 * margin
            columns = max(1, (usable_width + min_gap) // (cover_size + min_gap))
            gap = (usable_width - columns * cover_size) / (columns - 1) if columns > 1 else 0
            for start in range(0,len(self.rows),columns):
                rows=self.rows[start:start+columns]
                items = [(QRectF(margin + i * (cover_size + gap), y + 12, cover_size, cover_size), row)
                         for i, row in enumerate(rows)]
                blocks.append({'type':'albums','y':y,'h':164,'items':items})
                y+=164
                for cover, album in items:
                    amount = self.album_reveals.get(album['id'], 0)
                    if amount:
                        y = self.append_album_tracks(blocks, y, cover.center().x(),
                                                     self.album_tracks.get(album['id'], []), amount)
        else:
            letter=None
            for i,row in enumerate(self.rows):
                if self.view=='tracks' and not self.playlist_edit:
                    first=(row['title'].strip()[:1] or '#').upper()
                    if first!=letter:
                        blocks.append({'type':'heading','y':y,'h':24,'text':first});y+=24;letter=first
                kind='track' if self.view in ('tracks','playlist','add_songs') else self.view[:-1]
                height=30 if kind=='track' else 54
                blocks.append({'type':kind,'row':row,'y':y,'h':height,'index':i,'inline':False});y+=height
        self.blocks=blocks;self.setMinimumHeight(y);self.setMaximumHeight(max(y,0))
        self.update()

    def nearby_album_cover_keys(self, top, height):
        rows = [block for block in self.blocks if block['type'] == 'albums']
        first = bisect_right(rows, top, key=lambda row: row['y'] + row['h'])
        end = bisect_left(rows, top + height, key=lambda row: row['y'])
        nearby = (rows[first:end] + rows[end:end + PREFETCH_ALBUM_ROWS]
                  + rows[max(0, first - PREFETCH_ALBUM_ROWS):first][::-1])
        return [album_cover_key(album['id']) for row in nearby for _, album in row['items']]

    def cover(self,painter,rect,track_id,album_id=None):
        key = album_cover_key(album_id) if album_id is not None else track_id
        cover = self.load_cover(key, track_id)
        if cover and not cover.isNull():
            cropped=cover.scaled(round(rect.width()),round(rect.height()),Qt.KeepAspectRatioByExpanding,Qt.SmoothTransformation)
            x=(cropped.width()-rect.width())/2;y=(cropped.height()-rect.height())/2
            painter.drawPixmap(rect,cropped,QRectF(x,y,rect.width(),rect.height()))
        else:
            painter.fillRect(rect,gradient(rect,[(0,'#bcbcbc'),(1,'#868686')]))
            if album_id is None and key and key not in self.asked:
                self.asked.add(key)
                QTimer.singleShot(0,lambda i=key:self.covers_needed.emit([i]))
        if rect.width()>40:
            painter.setPen(QPen(QColor(0,0,0,52),1));painter.setBrush(Qt.NoBrush);painter.drawRect(rect.adjusted(.5,.5,-.5,-.5))
            painter.setPen(QColor(255,255,255,76));painter.drawLine(rect.topLeft(),rect.topRight())

    def load_cover(self, key, fallback=None):
        if key in self.covers:
            return self.covers[key]
        source = self.cover_sources.get(key) or self.cover_sources.get(fallback)
        if not source:
            return None
        pixmap = QPixmap()
        if isinstance(source, bytes):
            pixmap.loadFromData(source)
        else:
            pixmap.load(source)
        self.covers[key] = pixmap
        while len(self.covers)>64:self.covers.pop(next(iter(self.covers)))
        return pixmap

    def line(self,painter,rect,value,size=11,bold=False,color='#d7d7d7',align=Qt.AlignLeft):
        painter.setFont(font(size,bold,'Helvetica Neue'));painter.setPen(QColor(color))
        s=painter.fontMetrics().elidedText(str(value or ''),Qt.ElideRight,max(0,int(rect.width())))
        painter.drawText(rect,align|Qt.AlignVCenter,s)

    def checkbox(self,painter,rect,checked):
        painter.setPen(QPen(QColor('#afafaf'),1));painter.setBrush(Qt.NoBrush);painter.drawRect(rect.adjusted(.5,.5,-.5,-.5))
        if checked:
            painter.setPen(QPen(QColor('#ebebeb'),2,Qt.SolidLine,Qt.RoundCap,Qt.RoundJoin))
            path=QPainterPath();path.moveTo(rect.x()+3,rect.y()+6.5);path.lineTo(rect.x()+5.5,rect.y()+9);path.lineTo(rect.x()+10,rect.y()+3.5);painter.drawPath(path)

    def album_separator(self, painter, x, track_top):
        edge = track_top - .75
        outline = QPainterPath()
        outline.moveTo(0, edge)
        outline.lineTo(x - 8, edge)
        outline.lineTo(x, track_top - ALBUM_NOTCH_HEIGHT)
        outline.lineTo(x + 8, edge)
        outline.lineTo(self.width(), edge)

        fill = QPainterPath(outline)
        fill.lineTo(self.width(), track_top + 6)
        fill.lineTo(0, track_top + 6)
        fill.closeSubpath()
        painter.fillPath(fill, QColor(ALBUM_TRACK_FILL))

        painter.save()
        painter.setClipPath(fill, Qt.IntersectClip)
        painter.translate(0, 1)
        for width, alpha in ((10, 4), (8, 7), (6, 11), (4, 16), (2, 38)):
            painter.strokePath(outline, QPen(QColor(0, 0, 0, alpha), width,
                                            Qt.SolidLine, Qt.FlatCap, Qt.RoundJoin))
        painter.restore()
        painter.strokePath(outline, QPen(QColor('#585858'), 1.5,
                                        Qt.SolidLine, Qt.FlatCap, Qt.MiterJoin))

    def paintEvent(self,event):
        p=QPainter(self);p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(event.rect(),QColor('#464646'))
        for block in self.blocks:
            kind,y,h=block['type'],block['y'],block['h']
            notch_x = block.get('notch_x')
            top = y - ALBUM_NOTCH_HEIGHT - 1 if notch_x is not None else y
            if y+h<event.rect().top() or top>event.rect().bottom():continue
            r=QRectF(0,y,self.width(),h)
            if kind=='albums':
                for cover,row in block['items']:
                    self.cover(p,cover,row.get('artwork_track_id'),row['id'])
                    chosen=bool(self.album_tracks.get(row['id'])) and all(t['id'] in self.selected for t in self.album_tracks[row['id']])
                    if self.selecting:
                        if chosen:p.fillRect(cover,QColor(0,0,0,90))
                        image(self.renderers['minus' if chosen else 'plus'],p,QRectF(cover.right()-14,cover.top()-7,23,25))
                    self.line(p,QRectF(cover.x()-4,y+132,126,16),row['title'],14,True,'#ebebeb',Qt.AlignHCenter)
                    self.line(p,QRectF(cover.x()-4,y+148,126,14),row.get('album_artist',''),12,False,'#9c9c9c',Qt.AlignHCenter)
            elif kind=='heading':
                p.fillRect(r,gradient(r,[(0,'#5d5d5d'),(1,'#8f8f8f')]))
                self.line(p,r.adjusted(9,0,-9,0),block['text'],14,True,'white')
            elif kind=='track':
                row=block['row'];inline=block['inline'];i=block['index']
                clipped = 'visible_bottom' in block
                if clipped:
                    p.save()
                    p.setClipRect(QRectF(0, top, self.width(), block['visible_bottom'] - top), Qt.IntersectClip)
                p.fillRect(r,QColor('#2b2b2b' if i%2 else ALBUM_TRACK_FILL if inline else '#464646'))
                if notch_x is not None:
                    self.album_separator(p, notch_x, y)
                right=self.width()-(24 if self.overflow else 12)
                select=self.selecting or self.playlist_edit or self.view=='add_songs'
                if inline:
                    number_width = block['number_width']
                    self.line(p,QRectF(12,y,number_width,h),block['number'],12,align=Qt.AlignHCenter)
                    x = 12 + number_width + 8
                    if select:
                        self.checkbox(p,QRectF(x,y+(h-13)/2,13,13),row['id'] in self.selected);x+=26
                    self.line(p,QRectF(x,y,max(0,right-x-44),h),row['title'],12)
                    self.line(p,QRectF(right-35,y,35,h),duration(row['duration_ms']),12,align=Qt.AlignRight)
                else:
                    self.cover(p,QRectF(0,y,29,30),row['id'] if row.get('has_artwork') else None)
                    if select:
                        self.checkbox(p,QRectF(right-13,y+8.5,13,13),row['id'] in self.selected);right-=23
                    self.line(p,QRectF(right-32,y,32,h),duration(row['duration_ms']),11,align=Qt.AlignRight)
                    x=60;w=max(0,(right-44-x-24)/3)
                    for offset,key in enumerate(('title','album','artist')):
                        self.line(p,QRectF(x+offset*(w+12),y,w,h),row.get(key,''))
                if clipped:
                    p.restore()
            else:
                row=block['row'];i=block['index'];is_playlist=kind=='playlist'
                p.fillRect(r,QColor('#303030' if i%2 else '#3e3e3e'))
                p.setPen(QColor('#242424'));p.drawLine(0,y+h-1,self.width(),y+h-1)
                editing=is_playlist and self.editing and row.get('can_plan_delete')
                opened=editing and self.row_actions==row['id']
                x=42 if editing else 10;right=self.width()-(24 if self.overflow else 12)
                if editing:image(self.renderers['collapse' if opened else 'minus'],p,QRectF(9,y+14,23,25))
                if opened:right-=154
                subtitle=(f"{row.get('track_count') or 0} songs" if is_playlist else
                    f"{row.get('album_count',0)} albums · {row.get('track_count',0)} songs")
                self.line(p,QRectF(x,y+10,max(0,right-x-19),21),row.get('name',row.get('title','')),18,True,'#ebebeb')
                self.line(p,QRectF(x,y+28,max(0,right-x-19),16),subtitle,14,False,'#9c9c9c')
                if not opened:image(self.renderers['chevron'],p,QRectF(right-9,y+20,9,14))

    def hit(self,point):
        for b in self.blocks:
            if b['y']<=point.y()<b.get('visible_bottom', b['y']+b['h']):
                if b['type']=='albums':
                    for rect,row in b['items']:
                        if rect.adjusted(-4,-8,4,32).contains(point):return {'type':'album','row':row}
                elif 'row' in b:return b
        return None

    def mousePressEvent(self,event):
        hit=self.hit(event.position())
        if not hit:return
        row=hit['row'];kind=hit['type']
        if kind=='playlist' and self.editing and row.get('can_plan_delete'):
            self.action.emit({'action':'playlist_actions','id':row['id']});return
        self.action.emit({'action':kind,'id':row['id']})

    def keyPressEvent(self,event):
        items=[b for b in self.blocks if 'row' in b]
        if event.key() in (Qt.Key_Up,Qt.Key_Down):
            self.focus_index=max(0,min(len(items)-1,self.focus_index+(-1 if event.key()==Qt.Key_Up else 1)));self.update()
        elif event.key() in (Qt.Key_Space,Qt.Key_Return) and items:
            b=items[min(self.focus_index,len(items)-1)];self.action.emit({'action':b['type'],'id':b['row']['id']})
        else:super().keyPressEvent(event)
