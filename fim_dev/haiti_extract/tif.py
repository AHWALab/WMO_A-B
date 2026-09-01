"""Minimal TIFF reader: strip/tile offsets, float32/float64, plus geo tags."""
import struct

def tags(buf):
    bo = "<" if buf[:2] == b"II" else ">"
    magic, = struct.unpack(bo + "H", buf[2:4])
    big = magic == 43
    if big:
        off, = struct.unpack(bo + "Q", buf[8:16])
        n, = struct.unpack(bo + "Q", buf[off:off + 8]); p = off + 8; esz = 20
    else:
        off, = struct.unpack(bo + "I", buf[4:8])
        n, = struct.unpack(bo + "H", buf[off:off + 2]); p = off + 2; esz = 12
    TS = {1:1, 2:1, 3:2, 4:4, 5:8, 6:1, 7:1, 8:2, 9:4, 10:8, 11:4, 12:8, 16:8, 17:8, 18:8}
    TF = {1:"B", 3:"H", 4:"I", 16:"Q", 12:"d", 11:"f", 2:"s", 6:"b", 8:"h", 9:"i", 17:"q", 7:"B", 5:"I", 10:"i"}
    out = {}
    for i in range(n):
        e = buf[p + i * esz: p + (i + 1) * esz]
        if big:
            tag, typ = struct.unpack(bo + "HH", e[:4]); cnt, = struct.unpack(bo + "Q", e[4:12]); val = e[12:20]
        else:
            tag, typ = struct.unpack(bo + "HH", e[:4]); cnt, = struct.unpack(bo + "I", e[4:8]); val = e[8:12]
        sz = TS.get(typ, 1) * cnt
        if sz > len(val):
            vo, = struct.unpack(bo + ("Q" if big else "I"), val[:8 if big else 4])
            raw = buf[vo:vo + sz]
        else:
            raw = val[:sz]
        f = TF.get(typ)
        if typ == 2:
            out[tag] = raw.split(b"\x00")[0].decode("latin1", "ignore")
        elif typ == 5 or typ == 10:
            v = struct.unpack(bo + f * (cnt * 2), raw)
            out[tag] = [v[k] / v[k + 1] if v[k + 1] else 0 for k in range(0, len(v), 2)]
        elif f:
            out[tag] = list(struct.unpack(bo + f * cnt, raw))
        else:
            out[tag] = raw
    return bo, big, out


def describe(buf):
    bo, big, t = tags(buf)
    d = {"width": t[256][0], "height": t[257][0], "bits": t.get(258, [None])[0],
         "sample_format": t.get(339, [1])[0], "compression": t.get(259, [1])[0],
         "photometric": t.get(262, [None])[0], "planar": t.get(284, [1])[0],
         "samples": t.get(277, [1])[0], "bigtiff": big, "byteorder": bo}
    if 322 in t:
        d["tile"] = (t[322][0], t[323][0]); d["ntiles"] = len(t[324])
    if 278 in t:
        d["rows_per_strip"] = t[278][0]; d["nstrips"] = len(t[273])
    if 33550 in t: d["pixel_scale"] = t[33550]
    if 33922 in t: d["tiepoint"] = t[33922]
    if 42113 in t: d["nodata"] = t[42113]
    if 34737 in t: d["geo_ascii"] = t[34737][:120]
    if 34735 in t: d["geo_keys"] = t[34735][:40]
    d["_t"] = t
    return d


def read_float(buf):
    """(H, W) float32 array from an uncompressed strip TIFF, strips may be out of order."""
    import numpy as np
    d = describe(buf)
    assert d["compression"] == 1 and d["bits"] == 32 and d["sample_format"] == 3, d
    H, W, t = d["height"], d["width"], d["_t"]
    rps = t[278][0]
    off, cnt = t[273], t[279]
    dt = np.dtype("<f4" if d["byteorder"] == "<" else ">f4")
    a = np.empty((H, W), dtype=dt)
    mv = memoryview(buf)
    for i, (o, c) in enumerate(zip(off, cnt)):
        r0 = i * rps
        rows = min(rps, H - r0)
        a[r0:r0 + rows] = np.frombuffer(mv, dtype=dt, count=rows * W, offset=o).reshape(rows, W)
    return a, d
