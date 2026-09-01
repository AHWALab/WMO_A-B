"""IBF region configuration.

One YAML per region, same conventions as fim_utils.pipeline_pf: a plain
dict with defaults filled in, every tunable lives here and is echoed
into the cycle summary so a product can always be traced back to the
thresholds that made it.
"""

import os

try:
    import yaml
except ImportError as exc:  # pragma: no cover
    raise ImportError("ibf_utils requires PyYAML") from exc

# Warning colours of the SFFS / FGS flood risk matrix (Speight et al. 2018)
RISK_LEVELS = ["VERY LOW", "LOW", "MEDIUM", "HIGH"]
RISK_COLORS = ["#63BE5F", "#FFD500", "#F58220", "#DA291C"]
SEVERITY_LEVELS = ["Minimal", "Minor", "Significant", "Severe"]
LIKELIHOOD_LEVELS = ["Very Low", "Low", "Medium", "High"]

# MATRIX[likelihood_idx][severity_idx] -> risk level idx (paper Fig. 1)
DEFAULT_MATRIX = [
    [0, 0, 1, 1],   # Very Low likelihood
    [0, 1, 1, 2],   # Low
    [0, 1, 2, 2],   # Medium
    [0, 1, 2, 3],   # High
]

# Same likelihood bands as fim_utils.probability.DEFAULT_BANDS
DEFAULT_LIKELIHOOD_BANDS = {"very_low": [0.0, 0.2], "low": [0.2, 0.4],
                            "medium": [0.4, 0.6], "high": [0.6, 1.01]}

# IBF team's IWF thresholds (IBFv1.0 script), kept as the default
DEFAULT_IWF = {
    "IWF_Pop": {"suffix": "total_pop",
                "severe": {"absolute": 10, "percentage": 0.05},
                "lmh": {"absolute": 100, "percentage": 0.10}},
    "IWF_bld_cnt": {"suffix": "bldg_count",
                    "severe": {"absolute": 10, "percentage": 0.05},
                    "lmh": {"absolute": 100, "percentage": 0.10}},
    "IWF_bld_area_m2": {"suffix": "bldg_area_m2",
                        "severe": {"absolute": 1000, "percentage": 0.05},
                        "lmh": {"absolute": 5000, "percentage": 0.10}},
    "IWF_roads_m": {"suffix": "rd_len_m",
                    "severe": {"absolute": 1000, "percentage": 0.05},
                    "lmh": {"absolute": 1000, "percentage": 0.10}},
}


def load_ibf_config(path: str, root: str = None) -> dict:
    with open(path) as fh:
        cfg = yaml.safe_load(fh) or {}
    cfg["_root"] = os.path.abspath(
        root or cfg.get("root") or os.environ.get("TITO_FIM_ROOT") or ".")

    for key in ("region", "receptors"):
        if key not in cfg:
            raise ValueError(f"Missing '{key}' in {path}")

    rec = cfg["receptors"]
    for key in ("buildings", "roads", "admin"):
        if key not in rec:
            raise ValueError(f"Missing 'receptors.{key}' in {path}")
    rec["buildings"].setdefault("id_field", "id")
    rec["buildings"].setdefault("subtype_field", "subtype")
    rec["buildings"].setdefault("layer", "")
    rec["roads"].setdefault("id_field", "id")
    rec["roads"].setdefault("layer", "")
    rec["roads"].setdefault("class_field", "class")
    rec["roads"].setdefault("keep_classes", [])   # empty = keep all
    adm = rec["admin"]
    for key in ("source", "id_field", "population_field"):
        if key not in adm:
            raise ValueError(f"Missing 'receptors.admin.{key}' in {path}")
    adm.setdefault("name_field", "")
    adm.setdefault("layer", "")
    rec.setdefault("land_use", {})           # optional GHS BUILT-C FUN raster
    rec["land_use"].setdefault("source", "")
    # IBFv1.0 dasymetric weights, unchanged
    rec.setdefault("land_class_weights", {0: 0.1, 1: 0.9, 2: 0.0})
    rec.setdefault("subtype_weights", {"residential": 1.0})
    rec.setdefault("critical_subtypes", ["medical", "education", "civic"])
    rec.setdefault("domain_buffer_m", 250.0)
    rec.setdefault("cache_dir", "outputs/ibf_cache")
    rec.setdefault("work_crs", "")           # empty = CRS of the FIM products

    fp = cfg.setdefault("fim_products", {})
    # Directory holding one cycle's probability rasters. {cycle} and {mode}
    # are substituted; with the pipeline_pf layout this is e.g.
    #   outputs/fim/<region>/{cycle}/combined
    fp.setdefault("root", "")
    fp.setdefault("mode", "combined")        # pluvial | fluvial | combined
    fp.setdefault("prefer_overbank", False)
    fp.setdefault("patterns", [])            # extra custom regex patterns

    cl = cfg.setdefault("classification", {})
    cl.setdefault("likelihood_bands", dict(DEFAULT_LIKELIHOOD_BANDS))
    cl.setdefault("reporting_threshold", 0.05)
    # depth thresholds (m) that define the potential-impact severity axis;
    # each is matched to the closest available probability product
    # project default severity depths = the first three FIM depth
    # thresholds (10, 30, 70 cm), identical for every country; the IBFv1.0
    # Guatemala reference used 0.76 m for severe
    cl.setdefault("severity_thresholds_m",
                  {"minor": 0.10, "significant": 0.30, "severe": 0.70})
    cl.setdefault("severity_match_tolerance", 0.6)   # relative, warn above
    cl.setdefault("matrix", [list(r) for r in DEFAULT_MATRIX])
    # IBFv1.0 compatibility: hazard_flag = highest threshold with p >= cutoff
    cl.setdefault("hazard_flag_cutoff", 0.3)
    cl.setdefault("iwf", {k: dict(v) for k, v in DEFAULT_IWF.items()})

    out = cfg.setdefault("outputs", {})
    out.setdefault("root", "outputs/ibf/{region}")
    out.setdefault("append_cycle", True)
    cfg.setdefault("cycle_format", "%Y%m%d.%H%M%S")
    return cfg


def resolve(cfg: dict, path: str) -> str:
    if not path:
        return path
    return path if os.path.isabs(path) else os.path.join(cfg["_root"], path)
