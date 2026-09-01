"""Write the delivered MaximumDepth of every sample as a compact GeoTIFF.

Output: max_depth_only/<Site>/sample_NNNN_MaximumDepth.tif
uint16 CENTIMETRES (1 cm precision), deflate with horizontal predictor,
georeferenced (UTM 18N, EPSG 32618), one strip per row block. These files
replace the 457 GB Gris.zip and LaQuinte.zip for archival: everything else
in those deliveries (velocity, time step maps) is dropped by design.
Standard library plus numpy only. Resumable, rerun until DONE.
"""
import json, os, struct, sys, time, zlib
import numpy as np

B = os.path.expanduser("~/mnt/FIM_version/Data/_haiti_build")
OUT = os.path.expanduser("~/mnt/FIM_version/Data/Haitii/max_depth_only")
BUDGET = float(os.environ.get("BUDGET", "36"))
SITE = sys.argv[1]                       # Gris | LaQuinte
key = SITE.lower()
ROWS_PER_STRIP = 256


def geokeys():
    # projected CRS, pixel is area, EPSG 32618
    return [1, 1, 0, 3, 1024, 0, 1, 1, 1025, 0, 1, 1, 3072, 0, 1, 32618]


def write_tif(path, q, scale, tiepoint):
    H, W = q.shape
    strips = []
    for r0 in range(0, H, ROWS_PER_STRIP):
        block = q[r0:r0 + ROWS_PER_STRIP].astype("<u2")
        # horizontal predictor: difference along each row, uint16 wraparound
        d = block.copy()
        d[:, 1:] = (block[:, 1:] - block[:, :-1]).astype("<u2")
        strips.append(zlib.compress(d.tobytes(), 6))
    nstrips = len(strips)
    ascii_txt = b"WGS 84 / UTM zone 18N|WGS 84|\x00"
    gk = geokeys()

    # layout: header(8) IFD(2+n*12+4) tagdata... strips...
    tags = []          # (tag, type, count, value_or_offset_marker)
    extra = []         # blobs that need their own offset

    def tag(t, typ, cnt, val):
        tags.append([t, typ, cnt, val])

    tag(256, 3, 1, W); tag(257, 3, 1, H); tag(258, 3, 1, 16)
    tag(259, 3, 1, 8)                       # deflate
    tag(262, 3, 1, 1)                       # black is zero
    tag(273, 4, nstrips, "strip_offsets")
    tag(277, 3, 1, 1)
    tag(278, 3, 1, ROWS_PER_STRIP)
    tag(279, 4, nstrips, "strip_counts")
    tag(284, 3, 1, 1)
    tag(317, 3, 1, 2)                       # horizontal predictor
    tag(339, 3, 1, 1)                       # unsigned int
    tag(33550, 12, 3, "pixel_scale")
    tag(33922, 12, 6, "tiepoint")
    tag(34735, 3, len(gk), "geokeys")
    tag(34737, 2, len(ascii_txt), "geoascii")
    tags.sort(key=lambda x: x[0])

    n = len(tags)
    ifd_off = 8
    data_off = ifd_off + 2 + n * 12 + 4
    blobs = {}

    def put(name, payload):
        nonlocal data_off
        blobs[name] = (data_off, payload)
        data_off += len(payload) + (len(payload) & 1)

    so_pos = None
    put("pixel_scale", struct.pack("<3d", scale[0], scale[1], 0.0))
    put("tiepoint", struct.pack("<6d", 0, 0, 0, tiepoint[0], tiepoint[1], 0))
    put("geokeys", struct.pack("<%dH" % len(gk), *gk))
    put("geoascii", ascii_txt)
    if nstrips > 1:
        put("strip_offsets", b"\x00" * 4 * nstrips)
        put("strip_counts", struct.pack("<%dI" % nstrips, *[len(s) for s in strips]))
    strip0 = data_off
    offs = []
    for s in strips:
        offs.append(data_off)
        data_off += len(s)
    if nstrips > 1:
        blobs["strip_offsets"] = (blobs["strip_offsets"][0],
                                  struct.pack("<%dI" % nstrips, *offs))

    out = bytearray()
    out += b"II*\x00" + struct.pack("<I", ifd_off)
    out += struct.pack("<H", n)
    TS = {2: 1, 3: 2, 4: 4, 12: 8}
    for t, typ, cnt, val in tags:
        sz = TS[typ] * cnt
        if isinstance(val, str):
            if val == "strip_offsets" and nstrips == 1:
                enc = struct.pack("<I", offs[0])
            elif val == "strip_counts" and nstrips == 1:
                enc = struct.pack("<I", len(strips[0]))
            else:
                enc = struct.pack("<I", blobs[val][0])
        elif sz <= 4:
            f = {3: "<H", 4: "<I"}[typ]
            enc = struct.pack(f, val).ljust(4, b"\x00")
        else:
            raise ValueError
        out += struct.pack("<HHI", t, typ, cnt) + enc
    out += struct.pack("<I", 0)
    for name in sorted(blobs, key=lambda k: blobs[k][0]):
        off, payload = blobs[name]
        out += b"\x00" * (off - len(out))
        out += payload
    out += b"\x00" * (strip0 - len(out))
    for s in strips:
        out += s
    tmp = path + ".part"
    with open(tmp, "wb") as f:
        f.write(out)
    os.replace(tmp, path)
    return len(out)


st = json.load(open(os.path.join(B, "state_%s.json" % key)))["done"]
os.makedirs(os.path.join(OUT, SITE), exist_ok=True)
t0 = time.time()
done = skipped = 0
for smp in sorted(st):
    path = os.path.join(OUT, SITE, "%s_MaximumDepth.tif" % smp)
    if os.path.exists(path) and os.path.getsize(path) > 0:
        skipped += 1
        continue
    if time.time() - t0 > BUDGET:
        print("budget reached, rerun to continue (%d written, %d already)" % (done, skipped), flush=True)
        sys.exit(0)
    e = st[smp]
    with open(os.path.join(B, e["blob"]), "rb") as f:
        f.seek(e["start"])
        q = np.frombuffer(zlib.decompress(f.read(e["csize"])), dtype="<u2").reshape(e["H"], e["W"])
    scale = e.get("pixel_scale") or [2.0, 2.0]
    tp = e["tiepoint"][3:5]
    sz = write_tif(path, q, scale, tp)
    done += 1
print("DONE %s: %d written, %d already there" % (SITE, done, skipped), flush=True)
