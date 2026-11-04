"""iOS 6 library changes through MediaPlayer/MediaLibrary native methods."""
import plistlib
import uuid

from engine import Bridge, REMOTE, objc
from library_model import LibraryError, wire_id


def bridge_for(session):
    version = plistlib.loads(session.read('/System/Library/CoreServices/SystemVersion.plist'))
    if version.get('ProductVersion') != '6.1.6':
        raise LibraryError('unsupported_device', 'Library writes require the tested iPod iOS 6.1.6 backend.')
    lines = session.run('ps ax').splitlines()
    # Attaching does not reset StoreServices queues or restart an app.
    process = next((line.split()[0] for line in lines if '/Applications/Music~iphone.app/Music~iphone' in line), None)
    process = process or next((line.split()[0] for line in lines
        if '/System/Library/CoreServices/SpringBoard.app/SpringBoard' in line), None)
    if not process:
        raise LibraryError('device_unavailable', 'The iPod media process is unavailable.')
    bridge = Bridge(session)
    bridge.pid = process
    return bridge


def selection_script(tracks):
    identifiers = ','.join(objc(t['id']) for t in tracks)
    titles = ','.join(objc(t) for t in sorted({r['title'] for r in tracks}))
    # Every item is found by a narrow fresh query, then its exact NSNumber ID
    # string is checked. No 64-bit identity passes through a JS number.
    return '''
dlopen("/System/Library/Frameworks/MediaPlayer.framework/MediaPlayer",2);
var libraryWanted=[NSSet setWithArray:@[''' + identifiers + ''']];
var libraryTitles=@[''' + titles + '''];
var libraryItems=[NSMutableArray array];
var librarySeen=[NSMutableSet set];
for(var i=0;i<[libraryTitles count];i++){
 var query=[MPMediaQuery songsQuery];
 [query addFilterPredicate:[MPMediaPropertyPredicate predicateWithValue:[libraryTitles objectAtIndex:i] forProperty:@"title"]];
 var items=[query items];
 for(var j=0;j<[items count];j++){
  var it=[items objectAtIndex:j];
  var identity=[[it valueForProperty:@"persistentID"] description];
  if([libraryWanted containsObject:identity] && ![librarySeen containsObject:identity] && [it valueForProperty:@"assetURL"]){
   [librarySeen addObject:identity];[libraryItems addObject:it];
  }
 }
}
if([libraryItems count]!=[libraryWanted count]) throw new Error("Selected iPod entries changed");
'''


ENTITY = '''
function libraryIDBytes(number){
 var call=[NSInvocation invocationWithMethodSignature:[number methodSignatureForSelector:@selector(unsignedLongLongValue)]];
 [call setTarget:number];[call setSelector:@selector(unsignedLongLongValue)];[call invoke];
 var memory=malloc(8);[call getReturnValue:memory];
 var data=[NSData dataWithBytes:memory length:8];free(memory);return data;
}
var mediaLibrary=[[[it mediaLibrary] libraryDataProvider] library];
var entity=[ML3Track alloc];
var invocation=[NSInvocation invocationWithMethodSignature:[entity methodSignatureForSelector:@selector(initWithPersistentID:inLibrary:)]];
[invocation setTarget:entity];[invocation setSelector:@selector(initWithPersistentID:inLibrary:)];
var argument=malloc(8);
var identityData=libraryIDBytes([it valueForProperty:@"persistentID"]);
[identityData getBytes:argument length:8];
[invocation setArgument:argument atIndex:2];
[[NSArray arrayWithObject:mediaLibrary] getObjects:argument];
[invocation setArgument:argument atIndex:3];
[invocation invoke];free(argument);
var identityProperty=[ML3Track propertyForMPMediaEntityProperty:@"persistentID"];
if(![entity existsInLibrary] || ![libraryIDBytes([entity valueForProperty:identityProperty]) isEqualToData:identityData]){
 [entity release];throw new Error("Native iPod item identity differs");
}
'''


def apply(session, tracks, proposal, cover=None, genres=None):
    bridge = bridge_for(session)
    script = selection_script(tracks)
    remote = None
    try:
        if proposal['action'] == 'delete_tracks':
            script += '''
var libraryOK=[[MPMediaLibrary defaultMediaLibrary] removeItems:libraryItems];
if(!libraryOK) throw new Error("iPod rejected track deletion");
'''
        else:
            script += 'var libraryResolvedGenreID=nil;\n'
            script += 'for(var i=0;i<[libraryItems count];i++){ var it=[libraryItems objectAtIndex:i];\n'
            if proposal['action'] == 'replace_artwork':
                remote = REMOTE + '/library-cover-' + uuid.uuid4().hex + '.jpg'
                session.write(remote, cover, mobile=True)
                script += ENTITY
                script += 'var data=[NSData dataWithContentsOfFile:' + objc(remote) + '];\n'
                script += '''
var updated=[entity populateArtworkCacheWithArtworkData:data];
[entity release];
if(!updated) throw new Error("iPod rejected artwork update");
'''
            else:
                for field, value in proposal['changes'].items():
                    native = objc(value) if isinstance(value, str) else '[NSNumber numberWithInt:' + str(value) + ']'
                    if field == 'genre':
                        # MPMediaItem ignores this foreign collection property.
                        # Resolve existing rows by ID; newWithDictionary for an
                        # existing name can replace its row and orphan members.
                        script += ENTITY
                        existing = next((g for g in (genres or []) if g['name'] == value), None)
                        script += 'if(!libraryResolvedGenreID){\n'
                        if existing:
                            # Signed JSON keeps Foundation on its integer path.
                            script += 'libraryResolvedGenreID=[[[NSJSONSerialization JSONObjectWithData:[' + objc('[' + str(wire_id(existing['id'])) + ']') + ' dataUsingEncoding:4] options:0 error:NULL] objectAtIndex:0] retain];\n'
                        else:
                            # Resolve once for the entire selection. The native
                            # collection chooses its own ID; recreating it for
                            # each track could orphan earlier batch members.
                            script += 'var genre=[ML3Genre newWithDictionary:@{@"genre":' + native + '} inLibrary:mediaLibrary];\n'
                            script += 'if(![genre existsInLibrary] || ![[genre valueForProperty:@"genre"] isEqualToString:' + native + ']) throw new Error("New genre was not registered");\n'
                            script += 'libraryResolvedGenreID=[[genre valueForProperty:@"ROWID"] retain];[genre release];\n'
                        script += '}\n'
                        script += '''
var updated=libraryResolvedGenreID ? [entity setValue:libraryResolvedGenreID forProperty:[ML3Genre trackForeignPersistentID]] : NO;
[entity release];
if(!updated) throw new Error("iPod rejected genre update");
'''
                    else:
                        script += '[it setValue:' + native + ' forProperty:' + objc(field) + '];\n'
            script += '}\n[libraryResolvedGenreID release];\n'
        script += 'var imetalResult=@{@"submitted":[NSNumber numberWithUnsignedInt:[libraryItems count]]};\n'
        return bridge.execute(script, result=True)
    finally:
        if remote:
            try:
                session.sftp.remove(remote)
            except FileNotFoundError:
                pass


def artwork(session, track):
    remote = REMOTE + '/library-preview-' + uuid.uuid4().hex + '.png'
    script = selection_script([track]) + '''
var item=[libraryItems objectAtIndex:0];
var art=[item valueForProperty:@"artwork"];
var size=new CGSize;
size->width=600;size->height=600;
var image=art ? [art imageWithSize:*size] : nil;
var data=image ? UIImagePNGRepresentation(image) : nil;
var saved=data ? [data writeToFile:''' + objc(remote) + ''' atomically:YES] : NO;
var imetalResult=@{@"saved":[NSNumber numberWithBool:saved]};
'''
    try:
        result = bridge_for(session).execute(script, result=True)
        return session.read(remote) if result['saved'] else None
    finally:
        try:
            session.sftp.remove(remote)
        except FileNotFoundError:
            pass
