"""Deterministic, offline design-review data. Never used by device operations."""
from PySide6.QtGui import QPixmap
from library_widgets import ASSETS


def load_preview(window,state='albums'):
    pane=window.library
    pane.device={'id':'preview-touch','kind':'touch','name':'iPod touch 4'}
    names=['Celestial Blues','Morning Star','Boghz','Golgotha','Coil','Entwined','Psychic Wound','Ruse','Paradise Lost']
    tracks=[];albums=[]
    for a in range(15):
        for i,name in enumerate(names):
            tracks.append({'id':str(a*9+i+1),'title':name,'artist':'King Woman','album':'Celestial Blues',
                'album_artist':'King Woman','genre':'Doom Metal','year':2021,'duration_ms':276000,
                'disc_number':1,'track_number':i+1,'album_id':f'a{a}','artist_id':'king',
                'has_artwork':True,'can_plan_delete':True,'availability':'local','size_bytes':1000000})
        albums.append({'id':f'a{a}','title':'Celestial Blues','album_artist':'King Woman','track_count':9,
            'year':2021,'genre':'Doom Metal','has_artwork':True,'artwork_track_id':str(a*9+1)})
    pane.data={'snapshot_id':'preview','revision':'preview','tracks':tracks,'albums':albums,
        'artists':[{'id':k,'title':n,'track_count':count} for k,n,count in [('c418','C418',38),('king','King Woman',9),('metallica','Metallica',20),('strokes','The Strokes',11)]],
        'playlists':[{'id':str(i+1000),'name':n,'track_count':count,'members':[str(t+1) for t in range(count)],'can_plan_delete':True}
            for i,(n,count) in enumerate([('School days',3),('Celestial Blues',9),('Favourites',24)])]}
    pane.canvas.cover_sources={t['id']:str(ASSETS/'preview-cover.png') for t in tracks}
    pane.canvas.covers={t['id']:QPixmap(str(ASSETS/'preview-cover.png')) for t in tracks}
    pane.view='albums';pane.artist=None;pane.playlist=None;pane.expanded=None;pane.selecting=False;pane.selected.clear();pane.editing=False;pane.playlist_edit=False
    window.content_stack.setCurrentWidget(pane)
    if state in ('songs','songs-selected','playlist-picker','song-info'):pane.view='tracks'
    if state in ('songs','songs-selected','playlist-picker'):
        samples=[]
        for i in range(16):
            item=dict(tracks[i]);item['title']=('Ayayay' if i<8 else 'Byayay')+(f' {i%8+1}' if i%8 else '')
            samples.append(item)
        pane.data['tracks']=samples
    if state.startswith('playlist') or state=='no-playlists':pane.view='playlists'
    if state=='playlist-picker':pane.view='tracks'
    if state=='artists':pane.view='artists'
    if state in ('expanded','album-info'):pane.expanded='a0'
    if state in ('songs-selected','playlist-picker'):pane.selecting=True;pane.selected={'1','2'}
    if state in ('playlist-editing','playlist-actions','playlist-rename'):pane.editing=True
    if state in ('playlist-actions','playlist-rename'):pane.row_actions='1000'
    if state=='playlist-open':pane.playlist='1000';pane.view='playlist'
    if state=='no-playlists':pane.data['playlists']=[]
    pane.refresh()
    if state=='album-info':pane.album_info()
    if state=='song-info':pane.info(['1'],False)
    if state=='playlist-picker':pane.pick_playlist()
    if state=='playlist-rename':pane.rename_playlist()
    if state in ('loading','error','disconnected'):
        pane.show_state({'loading':'Loading library…','error':'Could not read library','disconnected':'Device disconnected'}[state],state=='error')
