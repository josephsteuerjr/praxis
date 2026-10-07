"""Repair nfpm's unsigned RPM archive-size metadata from decoded payload bytes.

RPM4.14 rpm2cpio checks this signature-header tag against decoded length.
Neither the signed/main header nor compressed payload is modified.
"""
import gzip
import lzma
import struct
from pathlib import Path


def _header(file, offset):
    file.seek(offset)
    raw = file.read(16)
    if len(raw) != 16 or raw[:4] != b"\x8e\xad\xe8\x01":
        raise ValueError("Invalid RPM header")
    count, size = struct.unpack(">II", raw[8:])
    if count > 100000 or size > 16 * 1024 * 1024:
        raise ValueError("Oversized RPM header")
    entries = [struct.unpack(">IIII", file.read(16)) for _ in range(count)]
    start = offset + 16 + count * 16
    store = file.read(size)
    if len(store) != size:
        raise ValueError("Truncated RPM header")
    return entries, store, start, start + size


def repair(path):
    path = Path(path)
    with path.open("rb") as file:
        if file.read(4) != b"\xed\xab\xee\xdb":
            raise ValueError("Not an RPM")
        signature, store, start, end = _header(file, 96)
        if any(tag in (1002, 1005, 267, 268) for tag, _typ, _offset, _count in signature):
            raise ValueError("Refusing to modify a signed RPM")
        main, data, _main_start, payload = _header(file, (end + 7) & ~7)
        compressor = next((data[offset:].split(b"\0", 1)[0].decode("ascii")
                           for tag, typ, offset, count in main if tag == 1125 and typ == 6), None)
        decoder = {"gzip": gzip.GzipFile, "xz": lzma.LZMAFile}.get(compressor)
        if decoder is None:
            raise ValueError("Unsupported RPM payload compression")
        file.seek(payload)
        size = 0
        with (gzip.GzipFile(fileobj=file, mode="rb") if compressor == "gzip" else decoder(file, mode="rb")) as stream:
            while chunk := stream.read(1024 * 1024):
                size += len(chunk)
        candidates = [(tag, typ, offset, count) for tag, typ, offset, count in signature if tag in (1007, 271)]
        if not candidates:
            raise ValueError("Missing RPM archive-size tag")
        updates = []
        for tag, typ, offset, count in candidates:
            fmt, width = (">I", 4) if tag == 1007 and typ == 4 else (">Q", 8) if tag == 271 and typ == 5 else (None, 0)
            if fmt is None or count != 1 or offset + width > len(store):
                raise ValueError("Invalid RPM archive-size tag")
            before = struct.unpack(fmt, store[offset:offset + width])[0]
            updates.append((start + offset, struct.pack(fmt, size), before))
    with path.open("r+b") as file:
        for offset, encoded, _before in updates:
            file.seek(offset)
            file.write(encoded)
    return {"decoded_bytes": size, "previous_sizes": [before for _, _, before in updates],
            "compression": compressor, "payload_offset": payload}
