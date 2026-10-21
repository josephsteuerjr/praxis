"""Regenerate the checked-in native icon using only the Python standard library."""
from pathlib import Path
import struct
import zlib

def chunk(kind, data):
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

def png(size):
    rows = bytearray()
    for y in range(size):
        rows.append(0)
        for x in range(size):
            a, b = (x + .5) / size, (y + .5) / size
            if (a-.5)**2+(b-.5)**2 > .22:
                rgba = (0,0,0,0)
            elif (.4<a<.6 and .22<b<.78) or (.22<a<.78 and .4<b<.6):
                rgba = (250,248,255,255)
            else:
                rgba = (114,96,236,255)
            rows.extend(rgba)
    return b"\x89PNG\r\n\x1a\n"+chunk(b"IHDR",struct.pack(">IIBBBBB",size,size,8,6,0,0,0))+chunk(b"IDAT",zlib.compress(rows))+chunk(b"IEND",b"")

root=Path(__file__).parent/"icons"
root.mkdir(exist_ok=True)
image=png(128)
(root/"icon.png").write_bytes(image)
(root/"icon.ico").write_bytes(struct.pack("<HHH",0,1,1)+struct.pack("<BBBBHHII",128,128,0,0,1,32,len(image),22)+image)
