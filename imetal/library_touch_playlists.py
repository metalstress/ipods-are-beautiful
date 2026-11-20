"""iOS 6 playlist access through native frameworks, with exact 64-bit IDs."""

from engine import objc
from library_model import LibraryError, capabilities, pid, seal, wire_id
from library_touch import bridge_for, ENTITY


def read(session, snapshot):
    script = '''
dlopen("/System/Library/Frameworks/MediaPlayer.framework/MediaPlayer",2);
var lists=[[MPMediaQuery playlistsQuery] collections];
var result=[NSMutableArray array];
for(var i=0;i<[lists count];i++){
 var p=[lists objectAtIndex:i];var ids=[NSMutableArray array];var items=[p items];
 for(var j=0;j<[items count];j++) [ids addObject:[[[items objectAtIndex:j] valueForProperty:@"persistentID"] description]];
 [result addObject:@{@"id":[[p valueForProperty:@"persistentID"] description],@"members":ids}];
}
var imetalResult=@{@"playlists":result};
'''
    records = bridge_for(session).execute(script, result=True)['playlists']
    members = {pid(r['id']): [pid(i) for i in r['members']] for r in records}
    for playlist in snapshot['playlists']:
        if playlist['id'] in members:
            playlist['track_ids'] = members[playlist['id']]
            playlist['track_count'] = len(playlist['track_ids'])
    known = all(p['track_ids'] is not None for p in snapshot['playlists']
                if p['kind'] == 'playlist' and not p['is_hidden'])
    snapshot['capabilities'] = capabilities('touch', known)
    return seal(snapshot)


def apply(session, snapshot, proposal):
    code = 'dlopen("/System/Library/Frameworks/MediaPlayer.framework/MediaPlayer",2);\n'
    if proposal['action'] == 'create_playlist':
        code += 'var playlist=[[MPMediaLibrary defaultMediaLibrary] addPlaylistWithName:' + objc(proposal['playlist_name']) + '];\n'
        code += 'if(!playlist) throw new Error("iPod rejected playlist creation");\n'
    else:
        code += 'var wanted=[NSSet setWithArray:@[' + objc(proposal['playlist_id']) + ',' + objc(str(wire_id(proposal['playlist_id']))) + ''']];
var lists=[[MPMediaQuery playlistsQuery] collections];var playlist=nil;
for(var i=0;i<[lists count];i++){
 var p=[lists objectAtIndex:i];
 if([wanted containsObject:[[p valueForProperty:@"persistentID"] description]]){playlist=p;break;}
}
if(!playlist) throw new Error("Selected iPod playlist changed");
'''
    if proposal['action'] == 'delete_playlist':
        code += 'if(![[MPMediaLibrary defaultMediaLibrary] removePlaylist:playlist]) throw new Error("iPod rejected playlist deletion");\n'
    elif proposal['action'] == 'rename_playlist':
        code += '[playlist setValue:' + objc(proposal['playlist_name']) + ' forProperty:@"name"];\n'
    elif proposal['action'] in ('add_to_playlist', 'remove_from_playlist', 'set_playlist_tracks') or proposal['member_ids']:
        # Reuse the precision-safe invocation recipe used for track metadata.
        code += ENTITY[ENTITY.index('function libraryIDBytes'):ENTITY.index('var mediaLibrary=')]
        code += '''
var mediaLibrary=[[[MPMediaLibrary defaultMediaLibrary] libraryDataProvider] library];
var entity=[ML3Container alloc];
var call=[NSInvocation invocationWithMethodSignature:[entity methodSignatureForSelector:@selector(initWithPersistentID:inLibrary:)]];
[call setTarget:entity];[call setSelector:@selector(initWithPersistentID:inLibrary:)];
var argument=malloc(8);var identityData=libraryIDBytes([playlist valueForProperty:@"persistentID"]);
[identityData getBytes:argument length:8];[call setArgument:argument atIndex:2];
[[NSArray arrayWithObject:mediaLibrary] getObjects:argument];[call setArgument:argument atIndex:3];
[call invoke];free(argument);
if(![entity existsInLibrary]) throw new Error("Native playlist identity changed");
var numbers=@[''' + ','.join(objc(str(wire_id(i))) for i in proposal['member_ids']) + '''];
var bytes=[NSMutableData data];
var memberArgument=malloc(8);
for(var i=0;i<[numbers count];i++){
 var scanner=[NSScanner scannerWithString:[numbers objectAtIndex:i]];
 if(![scanner scanLongLong:memberArgument] || ![scanner isAtEnd]){free(memberArgument);throw new Error("Invalid native song identity");}
 [bytes appendData:[NSData dataWithBytes:memberArgument length:8]];
}
free(memberArgument);
var ok=[numbers count] ? [entity setTracksWithPersistentIDs:[bytes bytes] count:[numbers count] notify:YES] : [entity removeAllTracks];
[entity release];if(!ok) throw new Error("iPod rejected playlist songs");
'''
    code += 'var imetalResult=@{@"created_id":' + (
        '[[playlist valueForProperty:@"persistentID"] description]' if proposal['action'] == 'create_playlist' else '@""') + '};\n'
    return bridge_for(session).execute(code, result=True)
