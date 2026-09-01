"""FIM analysis domain.

The domain of an IBF cycle is exactly the footprint of the FIM
probability products: receptors are preloaded nationally but clipped,
tagged and cached for this window. Any product raster serves as the
template; the georeferencing quirk of some legacy products (LOCAL_CS
labels on grids that are really Web Mercator) is coerced the same
pragmatic way the IBF team's script does, with a log line.
"""

from dataclasses import dataclass

import rasterio
from pyproj import CRS, Transformer


def coerce_crs(crs):
    """Interpret a LOCAL_CS raster CRS as EPSG:3857 (legacy FIM grids)."""
    if crs is None:
        raise ValueError("Raster has no CRS")
    text = str(crs)
    if "LOCAL_CS" in text:
        print("    ibf_utils.domain: LOCAL_CS georeferencing, interpreting as EPSG:3857")
        return CRS.from_epsg(3857)
    return CRS.from_user_input(crs)


@dataclass
class FimDomain:
    bounds: tuple          # (minx, miny, maxx, maxy) in crs
    crs: object            # pyproj CRS
    transform: object
    shape: tuple           # (rows, cols)
    path: str = ""

    @classmethod
    def from_raster(cls, path: str) -> "FimDomain":
        with rasterio.open(path) as src:
            return cls(bounds=tuple(src.bounds), crs=coerce_crs(src.crs),
                       transform=src.transform, shape=(src.height, src.width),
                       path=path)

    def bounds_in(self, crs, buffer_m: float = 0.0) -> tuple:
        """Domain bounds in another CRS, optionally buffered by buffer_m METERS.

        The buffer is applied AFTER reprojection, in the units of the target
        CRS (meters when projected, converted to degrees when geographic).
        The previous behaviour buffered in domain units before reprojecting,
        which turned 250 m into 250 DEGREES for the new EPSG:4326 FIM
        products: the domain window silently became country wide, receptor
        caches and cycle outputs bloated to every admin unit, and per cycle
        sampling looped over the whole national receptor stock.
        """
        dst = CRS.from_user_input(crs)
        if dst.equals(self.crs):
            b = tuple(self.bounds)
        else:
            tr = Transformer.from_crs(self.crs, dst, always_xy=True)
            b = tr.transform_bounds(*self.bounds)
        if buffer_m:
            pad = float(buffer_m) if dst.is_projected \
                else float(buffer_m) / 111320.0
            b = (b[0] - pad, b[1] - pad, b[2] + pad, b[3] + pad)
        return b

    def polygon_in(self, crs, buffer_m: float = 0.0):
        from shapely.geometry import box
        return box(*self.bounds_in(crs, buffer_m))
