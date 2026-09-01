"""Tests for tito_utils.ibf_utils (receptor IBF layer).

Geo deps (geopandas/pyogrio) are optional in some CI environments, so
every test that needs them importorskips.
"""

import os

import numpy as np
import pytest

from tito_utils.ibf_utils.sampling import (parse_probability_filename,
                                           _tag_from_meters)
from tito_utils.ibf_utils.config import DEFAULT_MATRIX
from tito_utils.ibf_utils.classify import likelihood_index

BANDS = {"very_low": [0.0, 0.2], "low": [0.2, 0.4],
         "medium": [0.4, 0.6], "high": [0.6, 1.01]}


# ---------------------------------------------------------------- filenames
def test_parse_new_naming():
    l = parse_probability_filename("prob_depth_ge_30cm.20230621.070000.tif")
    assert l.threshold_m == pytest.approx(0.30)
    assert l.tag == "30cm"
    assert l.cycle == "20230621.070000"
    assert not l.overbank


def test_parse_new_naming_overbank_and_p_tag():
    l = parse_probability_filename(
        "prob_depth_ge_7p62cm_overbank.20230620.000000.tif")
    assert l.threshold_m == pytest.approx(0.0762)
    assert l.overbank


def test_parse_legacy_qpeprob_units_not_mislabelled():
    """Regression for the IBFv1.0 defect: the 0.1524 m (6 in) grid was
    treated as the 7.62 cm layer. The parser must label it 15.24 cm."""
    l = parse_probability_filename("qpeprob.20230621.070000.0.1524 meters.tif")
    assert l.threshold_m == pytest.approx(0.1524)
    assert l.tag == "15p24cm"
    assert l.tag != "7p62cm"
    assert parse_probability_filename(
        "qpeprob.20230621.070000.0.3048 meters.tif").tag == "30p48cm"


def test_parse_rejects_other_files():
    assert parse_probability_filename("maxunitq.20230621.070000.tif") is None


def test_tag_roundtrip():
    assert _tag_from_meters(0.10) == "10cm"
    assert _tag_from_meters(0.76) == "76cm"
    assert _tag_from_meters(0.6096) == "60p96cm"


def test_discovery_reports_mixed_cycles(tmp_path):
    from tito_utils.ibf_utils.sampling import discover_probability_products
    for name in ["prob_depth_ge_10cm.20230621.070000.tif",
                 "prob_depth_ge_30cm.20230621.070000.tif",
                 "prob_depth_ge_76cm.20230620.000000.tif"]:
        (tmp_path / name).write_bytes(b"")
    layers, skipped, cycle = discover_probability_products(str(tmp_path))
    assert cycle == "20230621.070000"
    assert [l.tag for l in layers] == ["10cm", "30cm"]
    assert [s.cycle for s in skipped] == ["20230620.000000"]


# ---------------------------------------------------------------- matrix
def test_likelihood_bands_edges():
    assert likelihood_index(0.0, BANDS, 0.05) == -1        # no signal
    assert likelihood_index(0.04, BANDS, 0.05) == -1       # below reporting
    assert likelihood_index(0.1, BANDS, 0.05) == 0         # very low
    assert likelihood_index(0.2, BANDS, 0.05) == 1         # boundary -> upper
    assert likelihood_index(0.5, BANDS, 0.05) == 2
    assert likelihood_index(1.0, BANDS, 0.05) == 3


def test_matrix_invariants():
    for row in DEFAULT_MATRIX:
        assert row[0] == 0                                  # minimal is green
    assert DEFAULT_MATRIX[3][3] == 3                        # High x Severe = red
    assert DEFAULT_MATRIX[0][3] == 1                        # VL x Severe = yellow
    assert DEFAULT_MATRIX[1][3] == 2                        # Low x Severe = amber


# ---------------------------------------------------------------- end to end
@pytest.fixture()
def synthetic_region(tmp_path):
    gpd = pytest.importorskip("geopandas")
    rasterio = pytest.importorskip("rasterio")
    from rasterio.transform import from_origin
    from shapely.geometry import box, LineString

    crs = "EPSG:32615"
    transform = from_origin(500000, 1600100, 5, 5)          # 20x20 at 5 m

    def write(name, arr):
        path = tmp_path / name
        with rasterio.open(path, "w", driver="GTiff", height=20, width=20,
                           count=1, dtype="float32", crs=crs,
                           transform=transform) as dst:
            dst.write(arr.astype("float32"), 1)
        return str(path)

    p10 = np.zeros((20, 20)); p10[5:15, 5:15] = 0.7         # High for minor
    p30 = np.zeros((20, 20)); p30[8:12, 8:12] = 0.5         # Medium for signif
    p76 = np.zeros((20, 20)); p76[9:11, 9:11] = 0.3         # Low for severe
    write("prob_depth_ge_10cm.20230621.070000.tif", p10)
    write("prob_depth_ge_30cm.20230621.070000.tif", p30)
    write("prob_depth_ge_76cm.20230621.070000.tif", p76)

    lu = np.ones((20, 20)) * 1                              # all residential
    write("landuse.tif", lu)

    # receptors: one building in the deep core, one on the fringe, one dry
    b = gpd.GeoDataFrame(
        {"id": ["core", "fringe", "dry"],
         "subtype": ["residential", None, "medical"]},
        geometry=[box(500047, 1600047, 500053, 1600053),    # rows ~9-10
                  box(500030, 1600030, 500036, 1600036),
                  box(500002, 1600002, 500008, 1600008)],
        crs=crs)
    r = gpd.GeoDataFrame(
        {"id": ["r1"], "class": ["primary"], "subtype": ["road"]},
        geometry=[LineString([(500000, 1600050), (500100, 1600050)])], crs=crs)
    a = gpd.GeoDataFrame(
        {"CODIGO": [1], "MUNICIPIO": ["Test"], "POB": [1000.0]},
        geometry=[box(499900, 1599900, 500200, 1600200)], crs=crs)

    nat = tmp_path / "national.gpkg"
    b.to_file(nat, layer="buildings", driver="GPKG")
    r.to_file(nat, layer="roads", driver="GPKG")
    adm = tmp_path / "admin.gpkg"
    a.to_file(adm, layer="admin", driver="GPKG")

    cfg = {
        "_root": str(tmp_path),
        "region": "TestRegion",
        "receptors": {
            "buildings": {"source": str(nat), "layer": "buildings",
                          "id_field": "id", "subtype_field": "subtype"},
            "roads": {"source": str(nat), "layer": "roads", "id_field": "id",
                      "class_field": "class", "keep_classes": []},
            "admin": {"source": str(adm), "layer": "admin",
                      "id_field": "CODIGO", "name_field": "MUNICIPIO",
                      "population_field": "POB"},
            "land_use": {"source": str(tmp_path / "landuse.tif")},
            "land_class_weights": {0: 0.1, 1: 0.9, 2: 0.0},
            "subtype_weights": {"residential": 1.0},
            "critical_subtypes": ["medical"],
            "domain_buffer_m": 20.0,
            "cache_dir": str(tmp_path / "cache"),
            "work_crs": crs,
        },
        "fim_products": {"root": str(tmp_path), "mode": "combined",
                         "prefer_overbank": False, "patterns": []},
        "classification": {
            "likelihood_bands": {k: list(v) for k, v in BANDS.items()},
            "reporting_threshold": 0.05,
            "severity_thresholds_m": {"minor": 0.10, "significant": 0.30,
                                      "severe": 0.76},
            "severity_match_tolerance": 0.6,
            "matrix": [list(r_) for r_ in DEFAULT_MATRIX],
            "hazard_flag_cutoff": 0.3,
            "iwf": {"IWF_Pop": {"suffix": "total_pop",
                                "severe": {"absolute": 10, "percentage": 0.05},
                                "lmh": {"absolute": 100, "percentage": 0.10}}},
        },
        "outputs": {"root": str(tmp_path / "out"), "append_cycle": True},
        "cycle_format": "%Y%m%d.%H%M%S",
    }
    return cfg


def test_end_to_end_cycle(synthetic_region):
    pytest.importorskip("geopandas")
    from tito_utils.ibf_utils.pipeline_ibf import run_ibf_cycle

    s = run_ibf_cycle(synthetic_region, products_dir=synthetic_region["_root"],
                      verbose=False)
    assert s["status"] == "ok"
    assert s["cycle"] == "20230621.070000"
    assert [l["tag"] for l in s["layers"]] == ["10cm", "30cm", "76cm"]

    import geopandas as gpd
    out = os.path.join(synthetic_region["outputs"]["root"], "20230621.070000",
                       "ibf_receptors.20230621.070000.gpkg")
    b = gpd.read_file(out, layer="buildings_ibf")
    core = b.set_index("feature_id").loc["core"]
    fringe = b.set_index("feature_id").loc["fringe"]
    dry = b.set_index("feature_id").loc["dry"]

    # core: minor@High=yellow, significant@Medium=amber, severe@Low=amber
    assert core["risk_level"] == "MEDIUM"
    assert core["hazard_flag"] == 3                     # p76 = 0.3 >= cutoff
    # fringe: only minor axis (p10=0.7 High) -> yellow
    assert fringe["risk_level"] == "LOW"
    # dry building: green, critical tag preserved
    assert dry["risk_level"] == "VERY LOW"
    assert bool(dry["critical"]) is True

    # population conserved (single admin unit holds all of it)
    assert b["population_per_building"].sum() == pytest.approx(1000.0)
    # dasymetric weights: fringe has no subtype -> land-class weight path
    assert 0 < fringe["population_per_building"] < 1000

    a = gpd.read_file(out, layer="admin_ibf")
    assert a.iloc[0]["risk_class"] == 2                 # worst feature cell
    assert a.iloc[0]["IWF_Pop"] >= 1
    r = gpd.read_file(out, layer="roads_ibf")
    assert r.iloc[0]["risk_class"] >= 1                 # road crosses the core


def test_receptor_cache_reused(synthetic_region):
    pytest.importorskip("geopandas")
    from tito_utils.ibf_utils.pipeline_ibf import run_ibf_cycle
    from tito_utils.ibf_utils import receptors as rmod

    run_ibf_cycle(synthetic_region, products_dir=synthetic_region["_root"],
                  verbose=False)
    calls = {"n": 0}
    original = rmod._read_clip

    def counting(*a, **k):
        calls["n"] += 1
        return original(*a, **k)

    rmod._read_clip = counting
    try:
        s = run_ibf_cycle(synthetic_region,
                          products_dir=synthetic_region["_root"], verbose=False)
    finally:
        rmod._read_clip = original
    assert s["status"] == "ok"
    assert calls["n"] == 0                              # cache hit, no re-read
