# Reading only the max depth out of the Haiti deliveries

Gris.zip and LaQuinte.zip are 100.7 GB and 356.5 GB. Each sample folder holds
MaxVeloc-dept.zip and output-time-maps.zip, and only MaximumDepth.tif inside
the first one is needed. Extracting the deliveries to get at it would move
457 GB; these four scripts read the 11.2 GB of depth members in place and
leave everything else untouched.

    scan.py <Gris|LaQuinte>     read the outer zip's central directory once
                                and cache it as cd_<site>.json
    zr.py                       a byte range view of one STORED member of the
                                outer zip, so the nested MaxVeloc-dept.zip can
                                be opened without extracting it
    tif.py                      minimal TIFF reader: tags, geo tags, and the
                                float32 strips (which arrive OUT OF ORDER in
                                these files, so they are gathered by offset)
    extract.py <Gris|LaQuinte>  for every sample, inflate MaximumDepth.tif,
                                quantize to centimetres, deflate, append to a
                                blob, and record offset, grid, tie point, wet
                                bbox and md5 in state_<site>.json

extract.py is resumable and checkpoints after every sample; rerun it until it
prints DONE. Set BUDGET to the seconds it may spend per run. Needs only the
standard library plus numpy, so it runs where geo libraries are not installed.

The blobs and state files are then the input to fim_dev/build_haiti_stores.py.
