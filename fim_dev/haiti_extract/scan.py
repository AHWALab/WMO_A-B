import json, os, sys, zipfile
D = os.path.expanduser("~/mnt/FIM_version/Data/Haitii")
name = sys.argv[1]
out = os.path.expanduser("~/mnt/FIM_version/Data/_haiti_build/cd_%s.json" % name.lower())
z = zipfile.ZipFile(os.path.join(D, name + ".zip"))
info = []
for i in z.infolist():
    info.append({"n": i.filename, "s": i.file_size, "c": i.compress_size,
                 "t": i.compress_type, "h": i.header_offset})
json.dump(info, open(out, "w"))
print(name, "entries", len(info))
