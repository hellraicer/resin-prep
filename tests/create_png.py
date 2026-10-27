import struct,zlib,zipfile
from pathlib import Path
def chunk(k,data):
    return struct.pack('>I',len(data))+k+data+struct.pack('>I',zlib.crc32(k+data)&0xffffffff)
data=b''.join(b'\0'+bytes(255 if 16<=x<48 and 16<=y<48 else 0 for x in range(64)) for y in range(64))
png=b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',64,64,8,0,0,0,0))+chunk(b'IDAT',zlib.compress(data))+chunk(b'IEND',b'')
Path(__file__).with_name('fixture.png').write_bytes(png)
manifest='''<?xml version="1.0" encoding="utf-8"?><Manifest><CreatedBy>Resin Prep Test</CreatedBy><LayerHeight>0.05</LayerHeight><ResolutionX>64</ResolutionX><ResolutionY>64</ResolutionY><DisplayWidth>6.4</DisplayWidth><DisplayHeight>6.4</DisplayHeight><MachineZ>10</MachineZ></Manifest>'''
with zipfile.ZipFile(Path(__file__).with_name('fixture.zip'),'w') as archive:
    archive.writestr('manifest.uvtools',manifest)
    for index in range(1,5): archive.writestr(f'{index}.png',png)
