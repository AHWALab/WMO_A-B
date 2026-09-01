"""Unzip every scenario store shipped in this folder tree, once.

Stores travel through git as <name>.zarr.zip inside their country folder
(fim_store/<Region>/). Stores bigger than GitHub's per file comfort zone
are shipped as split parts named <name>.zarr.zip.part01, .part02, ...;
this script joins the parts into the single zip first (once), then
extracts every zip whose matching <name>.zarr folder does not exist yet.
Already extracted stores are skipped, so the script is safe to run any
number of times.

Usage, from the repository root or from fim_store/:

    python fim_store/unzip_stores.py
    python fim_store/unzip_stores.py Haiti
"""

import os
import re
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))


def _walk_zips(root):
    """Walk zip/part files only; never descend into extracted .zarr trees."""
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [
            d for d in dirnames
            if not d.endswith(".zarr")
            and not d.endswith(".partial")
            and not d.endswith(".joining")
        ]
        yield dirpath, dirnames, filenames


def join_parts(root):
    """<name>.zarr.zip.partNN -> <name>.zarr.zip (kept; parts left in place)."""
    groups = {}
    for dirpath, _dirnames, filenames in _walk_zips(root):
        for fn in filenames:
            m = re.match(r"^(.+\.zarr\.zip)\.part(\d+)$", fn)
            if m:
                groups.setdefault(os.path.join(dirpath, m.group(1)), []).append(
                    (int(m.group(2)), os.path.join(dirpath, fn)))
    for target, parts in sorted(groups.items()):
        parts.sort()
        total = sum(os.path.getsize(p) for _, p in parts)
        if os.path.exists(target) and os.path.getsize(target) == total:
            continue
        nums = [n for n, _ in parts]
        if nums != list(range(1, len(nums) + 1)):
            raise RuntimeError(f"missing part for {os.path.basename(target)}: have {nums}")
        print(f"joining {len(parts)} parts -> {os.path.relpath(target, HERE)}", flush=True)
        tmp = target + ".joining"
        with open(tmp, "wb") as out:
            for _, p in parts:
                with open(p, "rb") as f:
                    while True:
                        b = f.read(1 << 22)
                        if not b:
                            break
                        out.write(b)
        os.replace(tmp, target)


def extract_zip(zip_path, out_dir):
    print(f"extracting: {os.path.relpath(out_dir, HERE)}", flush=True)
    with zipfile.ZipFile(zip_path) as z:
        names = z.namelist()
        wrapped = all(n.split("/")[0] == os.path.basename(out_dir)
                      for n in names if n.strip("/"))
        dest_root = os.path.dirname(out_dir) if wrapped else out_dir
        for member in names:
            target = os.path.realpath(os.path.join(dest_root, member))
            safe_base = os.path.realpath(dest_root)
            if not target.startswith(safe_base + os.sep) and target != safe_base:
                raise RuntimeError(f"unsafe path inside {os.path.basename(zip_path)}: {member}")
        partial = out_dir + ".partial"
        if os.path.isdir(partial):
            import shutil
            shutil.rmtree(partial)
        if wrapped:
            tmp_root = partial + ".root"
            if os.path.isdir(tmp_root):
                import shutil
                shutil.rmtree(tmp_root)
            z.extractall(tmp_root)
            os.rename(os.path.join(tmp_root, os.path.basename(out_dir)), partial)
            import shutil
            shutil.rmtree(tmp_root, ignore_errors=True)
        else:
            z.extractall(partial)
        os.rename(partial, out_dir)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    root = HERE
    if argv:
        country = argv[0]
        cand = os.path.join(HERE, country)
        if not os.path.isdir(cand):
            raise SystemExit(f"no country folder: {cand}")
        root = cand
        print(f"only: {country}", flush=True)

    join_parts(root)
    done = skipped = 0
    for dirpath, _dirnames, filenames in _walk_zips(root):
        for fn in sorted(filenames):
            if not fn.endswith(".zarr.zip"):
                continue
            zip_path = os.path.join(dirpath, fn)
            out_dir = os.path.join(dirpath, fn[: -len(".zip")])
            rel = os.path.relpath(out_dir, HERE)
            if os.path.isdir(out_dir) and os.listdir(out_dir):
                print(f"already extracted: {rel}", flush=True)
                skipped += 1
                continue
            extract_zip(zip_path, out_dir)
            done += 1
    if done == 0 and skipped == 0:
        print("no .zarr.zip stores found under fim_store/; nothing to do")
    else:
        print(f"done: {done} extracted, {skipped} already in place", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
