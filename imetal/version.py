"""One version for the window footer and Windows executable metadata."""
APP_NAME = "iPods Are Beautiful & I'm No Longer Afraid to Die"
VERSION = "1.0.3"


def windows_resource():
    number = tuple(int(n) for n in VERSION.split('.')) + (0,)
    return f'''VSVersionInfo(
  ffi=FixedFileInfo(filevers={number!r},prodvers={number!r},mask=0x3f,flags=0,OS=0x40004,fileType=1,subtype=0,date=(0,0)),
  kids=[StringFileInfo([StringTable('040904B0',[
    StringStruct('FileDescription',{APP_NAME!r}),
    StringStruct('ProductName',{APP_NAME!r}),
    StringStruct('InternalName','iPodsAreBeautiful'),
    StringStruct('OriginalFilename','iPods Are Beautiful.exe'),
    StringStruct('FileVersion',{(VERSION + '.0')!r}),
    StringStruct('ProductVersion',{VERSION!r})
  ])]),VarFileInfo([VarStruct('Translation',[1033,1200])])]
)'''
