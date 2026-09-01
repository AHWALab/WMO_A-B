"""Byte range view of a STORED member inside a big zip, so nested zips can be
read without extracting anything."""
import io, json, os, struct, zipfile

D = os.path.expanduser("~/mnt/FIM_version/Data/Haitii")
B = os.path.expanduser("~/mnt/FIM_version/Data/_haiti_build")


class Slice(io.RawIOBase):
    def __init__(self, path, start, length):
        self.f = open(path, "rb"); self.start = start; self.length = length; self.pos = 0
    def readable(self): return True
    def seekable(self): return True
    def seek(self, off, whence=0):
        if whence == 0: self.pos = off
        elif whence == 1: self.pos += off
        else: self.pos = self.length + off
        self.pos = max(0, min(self.length, self.pos))
        return self.pos
    def tell(self): return self.pos
    def read(self, n=-1):
        if n is None or n < 0: n = self.length - self.pos
        n = min(n, self.length - self.pos)
        if n <= 0: return b""
        self.f.seek(self.start + self.pos); b = self.f.read(n); self.pos += len(b); return b
    def readinto(self, buf):
        b = self.read(len(buf)); buf[:len(b)] = b; return len(b)
    def close(self):
        try: self.f.close()
        finally: super().close()


def data_offset(path, header_offset):
    """Start of the member payload, from its local file header."""
    with open(path, "rb") as f:
        f.seek(header_offset)
        h = f.read(30)
        assert h[:4] == b"PK\x03\x04", h[:4]
        n, m = struct.unpack("<HH", h[26:30])
    return header_offset + 30 + n + m


def outer(name):
    return os.path.join(D, name + ".zip"), json.load(open(os.path.join(B, "cd_%s.json" % name.lower())))


def nested(name, sample, member="MaxVeloc-dept.zip"):
    """(Slice, ZipFile) for one sample's nested zip."""
    path, cd = outer(name)
    key = "%s/%s/%s" % (name, sample, member)
    e = next(x for x in cd if x["n"] == key)
    assert e["t"] == 0, "outer member is compressed, byte range read not possible"
    s = Slice(path, data_offset(path, e["h"]), e["s"])
    return s, zipfile.ZipFile(s)
